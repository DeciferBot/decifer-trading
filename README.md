# Decifer Trading 2.0 — Autonomous Trading System

**Invented by: AMIT CHOPRA**

AI-powered autonomous trading bot built on Claude + Interactive Brokers. Uses a 6-agent multi-perspective intelligence system to scan, analyse, and execute trades across stocks and options. Includes ML learning loop, social sentiment tracking, smart order execution, portfolio optimization, and real-time IBKR streaming.

This document is the master rebuild guide. If everything is lost, follow this document to reconstruct the entire system from scratch.

## Prerequisites

Before anything else, you need the following installed on your Mac:

- **Python 3.11** — the bot runs on Python 3.11 (`/usr/local/bin/python3`). Homebrew install: `brew install python@3.11`
- **TA-Lib C library** — required by the `TA-Lib` Python package. Install via Homebrew: `brew install ta-lib`
- **TWS or IB Gateway** — Interactive Brokers desktop app, running on port 7496 (TWS) or 4001 (Gateway). API connections must be enabled in TWS settings (File > Global Configuration > API > Settings > Enable ActiveX and Socket Clients, check "Allow connections from localhost only")
- **Anthropic API Key** — get one from https://console.anthropic.com. Store in `.env` file (see below)

## Installation (From Scratch)

```bash
# 1. Clone or copy all project files into a folder
mkdir -p ~/Documents/Claude/Projects/decifer\ trading
cd ~/Documents/Claude/Projects/decifer\ trading

# 2. Create .env file with your API key
cat > .env << 'EOF'
ANTHROPIC_API_KEY=sk-ant-your-key-here
EOF

# 3. Install ALL Python dependencies (must use python3 -m pip to ensure correct Python)
python3 -m pip install -r requirements.txt

# 4. Download NLTK VADER lexicon (used by social sentiment)
python3 -c "import nltk; nltk.download('vader_lexicon')"

# 5. Make launcher executable
chmod +x launch_decifer.command

# 6. Start TWS or IB Gateway (port 7496)
#    - Paper account: DUP481326
#    - Live accounts: U3059777, U24093086

# 7. Launch
./launch_decifer.command

# Or manually:
python3 bot.py
```

## Complete Dependency List (requirements.txt)

Every Python package used by the system. If rebuilding, install ALL of these:

| Package | Version | Used By | Purpose |
|---------|---------|---------|---------|
| `ib_async` | >=0.9.86 | bot.py, orders.py, scanner.py, ibkr_streaming.py, smart_execution.py | IBKR API connection |
| `anthropic` | >=0.25.0 | agents.py, learning.py, news.py, sentinel_agents.py | Claude AI API |
| `yfinance` | >=0.2.40 | signals.py, scanner.py, options.py, options_scanner.py, data_collector.py, portfolio_optimizer.py | Market data (free) |
| `pandas` | >=2.0.0 | signals.py, data_collector.py, ml_engine.py, backtester.py, portfolio_optimizer.py, ibkr_streaming.py | Data analysis |
| `numpy` | >=1.24.0 | signals.py, options.py, options_scanner.py, ml_engine.py, backtester.py, portfolio_optimizer.py, smart_execution.py, ibkr_streaming.py | Numerical computation |
| `schedule` | >=1.2.0 | bot.py | Task scheduling |
| `colorama` | >=0.4.6 | bot.py | Terminal color output |
| `pytz` | >=2024.1 | risk.py, backtester.py | Timezone handling |
| `TA-Lib` | >=0.4.28 | signals.py | Technical indicators (requires C library: `brew install ta-lib`) |
| `requests` | >=2.31.0 | news.py, news_sentinel.py, social_sentiment.py | HTTP requests |
| `tradingview-screener` | >=0.3.0 | scanner.py | TradingView data (free, no API key) |
| `py_vollib` | >=1.0.1 | options.py | Options Greeks (Black-Scholes) |
| `pyarrow` | >=14.0.0 | data_collector.py | Parquet file format for ML training data |
| `scikit-learn` | >=1.3.0 | ml_engine.py | ML models (RandomForest, GradientBoosting) |
| `joblib` | >=1.3.0 | ml_engine.py | ML model persistence (save/load .pkl files) |
| `nltk` | >=3.8.0 | social_sentiment.py | VADER sentiment analysis |

**Standard library modules used** (no install needed): `sys`, `os`, `json`, `re`, `time`, `logging`, `threading`, `hashlib`, `importlib`, `pickle`, `argparse`, `warnings`, `asyncio`, `statistics`, `xml.etree.ElementTree`, `email.utils`, `http.server`, `collections`, `dataclasses`, `datetime`, `pathlib`, `typing`, `enum`, `functools`, `zoneinfo`

**System-level dependencies** (install via Homebrew before pip):
- `ta-lib` — C library for TA-Lib Python wrapper: `brew install ta-lib`

## Architecture

### Scan Pipeline (every 3-5 minutes during market hours)

1. **TradingView Screener** (`scanner.py`) — 6 regime-weighted queries build a dynamic universe of 50-100 symbols
2. **TV Pre-Filter** (`bot.py`) — rank by |signal| x relative volume, VWAP-confirmed, top 25 candidates. Thresholds: RSI dead zone 47-53, volume floor 0.5x, change floor 0.1%, recommendation floor 0.05
3. **News Sentiment** (`news.py`) — Yahoo RSS + keyword scoring + Claude confirmation for top scorers
4. **Social Sentiment** (`social_sentiment.py`) — Reddit JSON API + ApeWisdom + VADER, tracks mention velocity (acceleration)
5. **8-Dimension Scoring** (`signals.py`) — parallel ProcessPoolExecutor scores candidates across 5m/1D/1W timeframes
6. **Options Scan** (`options_scanner.py`) — unusual volume, IV rank, earnings plays, call/put skew
7. **6-Agent Pipeline** (`agents.py`) — consensus-based trade decision (requires 2+ agents to agree in paper mode)
8. **Execution** (`orders.py`) — multi-source price validation, IBKR order placement

### 6-Agent Claude Pipeline

Runs on every scored candidate that passes threshold:

1. **Technical Analyst** — price action, volume, indicators across 5m / 1D / 1W timeframes
2. **Macro Analyst** — regime classification, VIX, cross-asset dynamics, news flow
3. **Opportunity Finder** — synthesises technical + macro + options flow into top 3 trades
4. **Devil's Advocate** — argues against every proposed trade
5. **Risk Manager** — veto power, position sizing, portfolio-level risk checks
6. **Final Decision Maker** — outputs executable JSON trade instructions

Uses `claude-sonnet-4-6` model, 800 max tokens per agent call. Requires `ANTHROPIC_API_KEY` in `.env`.

### News Sentinel (Independent Pipeline)

A secondary 3-agent pipeline runs independently every 45 seconds:

1. **Catalyst Analyst** — evaluates news materiality
2. **Risk Gate** — checks portfolio impact
3. **Instant Decision** — go/no-go within 15-30 seconds

Monitors 80 symbols across holdings, 9 predefined themes (AI/Semis, EV, Biotech, etc.), and trending themes. Uses Yahoo RSS + Finviz + IBKR news. 10-minute cooldown between triggers, max 3 trades/hour, 0.75x position sizing.

### Signal Engine (8 Dimensions, scored 0-60)

Each candidate is scored across 8 independent dimensions in `signals.py`:

| # | Dimension | Indicator | Score Range |
|---|-----------|-----------|-------------|
| 1 | Trend | EMA(9/21/50) alignment + ADX strength | 0-10 |
| 2 | Momentum | MFI (volume-weighted RSI) | 0-10 |
| 3 | Squeeze | Bollinger Bands inside Keltner Channels | 0-10 |
| 4 | Flow | VWAP position + OBV divergence | 0-10 |
| 5 | Breakout | Donchian channel breach + volume | 0-7 |
| 6 | Confluence | Multi-timeframe agreement (5m + 1D + 1W) | 0-5 |
| 7 | News | Keyword scoring + Claude sentiment | 0-5 |
| 8 | Social | Reddit/ApeWisdom mention velocity + VADER | 0-10 |

Plus a +3 candlestick pattern bonus. Total possible: ~60. Minimum to trade: 18 (paper) / 28 (live).

### Regime Detection

Based on VIX levels and SPY trend:

| Regime | VIX Range | Score Threshold (paper, base=18) |
|--------|-----------|----------------------------------|
| BULL_TRENDING | < 15 | 18 (base) |
| BEAR_TRENDING | 15-35 | 15 (base - 3) |
| CHOPPY | 15-25 | 12 (base - 6) |
| PANIC | > 35 | 99 (no trades) |
| UNKNOWN | — | 15 (base - 3) |

### Parallel Scoring (ProcessPoolExecutor)

`score_universe()` in `signals.py` uses `ProcessPoolExecutor` (not ThreadPoolExecutor) to avoid yfinance's thread-safety bug (GitHub issue #2557). Each worker process gets its own memory space, eliminating cross-symbol data contamination from yfinance's shared `_DFS` global dict. Falls back to sequential automatically if multiprocessing fails. Workers: `min(6, cpu_count - 1)`.

## Complete File Reference

Every file in the project, what it does, and what it depends on:

### Core Files (required for bot to run)

| File | Lines | Purpose | Key Dependencies |
|------|-------|---------|-----------------|
| `bot.py` | ~2300 | Main orchestrator — scan loop, execution, dashboard server, hot reload | All other modules |
| `config.py` | ~300 | All settings in one dict. Only file to edit for behavior changes | `os` (stdlib only) |
| `signals.py` | ~800 | 8-dimension signal engine with ProcessPoolExecutor parallel scoring | yfinance, numpy, pandas, TA-Lib, config |
| `scanner.py` | ~500 | Dynamic universe builder via TradingView Screener API | tradingview-screener, ib_async, config |
| `agents.py` | ~650 | 6-agent Claude analysis pipeline (parallel agent 1+2) | anthropic, config |
| `orders.py` | ~1000 | IBKR order execution — limit, market, OCO brackets, options | ib_async, config, risk, learning |
| `options.py` | ~550 | Options chain analysis, IV rank, strike selection, Greeks | yfinance, py_vollib, config |
| `options_scanner.py` | ~500 | Proactive unusual options volume/IV/earnings scanner | yfinance, numpy, pandas, config |
| `risk.py` | ~350 | 5-layer risk management — hardcoded, agents cannot override | pytz, config |
| `learning.py` | ~400 | Trade logging, performance tracking, Claude weekly review | anthropic, config |
| `news.py` | ~500 | News sentiment — Yahoo RSS + keyword scoring + Claude deep read | requests, anthropic, config |
| `news_sentinel.py` | ~700 | Real-time news monitoring (independent of scan loop) | requests, threading, config, news |
| `sentinel_agents.py` | ~350 | 3-agent lightweight pipeline for news-triggered trades | anthropic, config |
| `theme_tracker.py` | ~550 | Market theme/narrative tracking (3-layer universe) | config |
| `dashboard.py` | ~2700 | Live web dashboard HTML/CSS/JS (served on port 8080) | None (pure HTML string) |
| `patch.py` | ~50 | Hot-patch utility — apply code fixes without restart | sys, os |

### Phase 2-5 Modules (new features)

| File | Lines | Purpose | Key Dependencies |
|------|-------|---------|-----------------|
| `social_sentiment.py` | ~750 | Reddit JSON API + ApeWisdom + VADER sentiment tracking | requests, nltk, threading |
| `ml_engine.py` | ~950 | ML learning loop — RandomForest + GradientBoosting, walk-forward CV | scikit-learn, joblib, numpy, pandas, config |
| `data_collector.py` | ~540 | Historical data downloader (yfinance + Stooq), Parquet storage | yfinance, pandas, numpy, pyarrow |
| `backtester.py` | ~850 | Walk-forward bar-by-bar backtesting engine | numpy, pandas, pytz, config, signals |
| `smart_execution.py` | ~830 | TWAP/VWAP/Iceberg execution strategies + analytics | ib_async, statistics, asyncio |
| `portfolio_optimizer.py` | ~870 | Correlation-aware sizing, risk parity, VaR, sector monitoring | yfinance, numpy, pandas |
| `ibkr_streaming.py` | ~670 | Real-time IBKR quote streaming, bar aggregation, smart data routing | ib_async, numpy, pandas, threading |

### Support Files

| File | Purpose |
|------|---------|
| `signals_integration_example.py` | Example code showing how to integrate signals.py |
| `launch_decifer.command` | macOS double-click launcher (bash script) |
| `.env` | API keys — `ANTHROPIC_API_KEY=sk-ant-...` (never commit) |
| `.gitignore` | Excludes .env, data/*.csv, data/*.parquet, logs/, __pycache__ |
| `requirements.txt` | All pip dependencies with version pins |

### Data Directory Structure

```
data/
├── trades.json            # Every trade: entry/exit price, P&L, agent reasoning
├── orders.json            # Every order sent to IBKR
├── equity_history.json    # Equity curve snapshots
├── favourites.json        # Watchlist symbols
├── saved_settings.json    # Dashboard-modified settings (persists across restarts)
├── custom_themes.json     # User-defined theme watchlists
└── historical/            # ML training data (Parquet format)
    ├── intraday/          # 5m bars (60-day rolling window per symbol)
    │   ├── AAPL_5m.parquet
    │   ├── TSLA_5m.parquet
    │   └── ...
    └── daily/             # Full daily OHLCV (20+ years per symbol)
        ├── AAPL_1d.parquet
        ├── TSLA_1d.parquet
        └── ...
```

### Documentation Directory

```
docs/
├── CHANGELOG.md                    # Version history and change log
├── CONFIG_GUIDE.md                 # All configuration parameters explained
├── STRATEGY.md                     # Complete strategy and signal documentation
├── DECISIONS.md                    # Architectural decision log with rationale
├── ML_ENGINE_GUIDE.md              # ML pipeline documentation
├── ML_ENGINE_SUMMARY.md            # ML engine overview
├── ML_ENGINE_QUICKSTART.txt        # Quick start for ML features
├── SOCIAL_SENTIMENT_README.md      # Social sentiment module documentation
├── SOCIAL_SENTIMENT_QUICKSTART.md  # Quick start for social sentiment
├── SOCIAL_SENTIMENT_INTEGRATION.md # Integration guide for social sentiment
├── Decifer_Product_Document.docx   # Original product spec
├── Decifer_Architecture.docx       # Original architecture doc
├── Decifer_v3_Architecture.docx    # v3 architecture update
└── Decifer_v3_Documentation.docx   # v3 full documentation
```

## Launcher Script (launch_decifer.command)

The macOS launcher does the following in order:
1. `cd` to its own directory (so it works from Finder double-click)
2. Loads `.env` variables (ANTHROPIC_API_KEY)
3. Runs `pip3 install -q -r requirements.txt` (fast no-op if up to date)
4. Opens `http://localhost:8080` in browser after 3 seconds
5. Runs `python3 bot.py`

To use: double-click `launch_decifer.command` in Finder, or from Terminal:
```bash
cd "/Users/amitchopra/Documents/Claude/Projects/decifer trading"
./launch_decifer.command
```

## Bot Startup Sequence (bot.py)

When `python3 bot.py` runs, the following happens in order:

1. Load `config.py` — all settings into `CONFIG` dict
2. Connect to IBKR (port 7496, client ID 10)
3. Sync positions and orders from IBKR
4. Load saved dashboard settings from `data/saved_settings.json`
5. Start HTTP dashboard server on port 8080
6. Download NLTK VADER lexicon (for social sentiment)
7. Start **News Sentinel** background thread (polls every 45s)
8. Start **Social Sentiment** polling thread (Reddit + ApeWisdom, 60s interval)
9. Start **ML Engine** (if scikit-learn installed and `ml_enabled=True`)
10. Start **Background Data Collector** daemon thread (downloads 61 symbols to Parquet)
11. Enter main scan loop (3-min intervals during prime hours)

## IBKR Connection Details

| Setting | Value | Notes |
|---------|-------|-------|
| Host | 127.0.0.1 | localhost only |
| Port | 7496 | TWS. Use 4001 for IB Gateway |
| Client ID | 10 | Must be unique if running multiple bots |
| Paper Account | DUP481326 | Currently active |
| Live Accounts | U3059777, U24093086 | Switch in config.py `active_account` |

**Important**: TWS must have API connections enabled: File > Global Configuration > API > Settings > Enable ActiveX and Socket Clients. Check "Allow connections from localhost only."

## Switching Paper to Live

1. In `config.py`, change `active_account` from `DUP481326` to your live account
2. Update risk parameters (see comments in config.py for live values):
   - `min_score_to_trade`: 18 → 28
   - `agents_required_to_agree`: 2 → 4
   - `max_positions`: 20 → 12
   - `daily_loss_limit`: 0.10 → 0.06
   - `risk_pct_per_trade`: 0.03 → 0.04
   - `consecutive_loss_pause`: 8 → 5
   - `scan_interval_prime`: 3 → 5
3. Restart the bot

Full paper vs live comparison in `docs/CONFIG_GUIDE.md`.

## Collecting Historical Data for ML Training

The data collector runs automatically as a background thread on bot startup (collecting 61 symbols). You can also run it manually:

```bash
# Download all data (61 symbols, daily + intraday)
python3 data_collector.py

# Specific symbols only
python3 data_collector.py --symbols AAPL TSLA NVDA

# Daily data only (faster, longer history — 20+ years)
python3 data_collector.py --daily-only

# Intraday only (5m bars, last 60 days)
python3 data_collector.py --intraday-only
```

Data sources (all free, no API key):
1. **yfinance** — 5m bars (60-day limit) + daily bars (max history)
2. **Stooq** — backup daily OHLCV via CSV endpoint
3. **Yahoo Finance fundamentals** — via yfinance

Data is saved as Parquet files in `data/historical/`. Each file includes pre-computed ML features: returns, ATR, EMAs, RSI, MFI, Bollinger Band position, VWAP distance, volume ratio, and regime labels.

Default 61-symbol universe covers: FAANG+, semiconductors, financials, healthcare, energy, consumer, industrials, ETFs (SPY, QQQ, IWM, XLF, XLE, XLK, XLV, GLD, TLT), and meme stocks (GME, AMC, PLTR, SOFI, RIVN, LCID).

```python
# Load training data in Python
from data_collector import get_training_dataset
df = get_training_dataset(interval="1d")  # or "5m" for intraday
```

## ML Engine

The ML pipeline in `ml_engine.py` provides:
- **TradeLabeler** — labels historical trades as win/loss with configurable thresholds
- **DeciferML** — RandomForest classifier + GradientBoosting regressor
- **SignalEnhancer** — multiplies signal scores by 0.5x-1.5x based on ML confidence
- **RegimeClassifier** — ML-based regime detection
- Walk-forward cross-validation using `TimeSeriesSplit`

Config in `config.py`: `ml_enabled=True`, `ml_min_trades=50` (minimum trades before training), `ml_retrain_interval=7` (days), `ml_confidence_weight=0.3` (30% ML influence on scores).

Models saved as `.pkl` files in `data/models/` via joblib.

## Social Sentiment

`social_sentiment.py` tracks Reddit and ApeWisdom for stock mentions:
- **Reddit JSON API** — scrapes r/wallstreetbets, r/stocks, r/investing (no API key needed)
- **ApeWisdom** — aggregated Reddit mention counts
- **VADER** — sentiment analysis with 90-word custom finance lexicon
- Tracks **mention velocity** (acceleration of mentions, not raw count)
- Background polling every 60 seconds
- Returns 0-10 score per symbol (fed into dimension #8 of signal engine)

## Dashboard

Live at `http://localhost:8080` — shows positions, agent reasoning, P&L, regime, scan status, equity curve, and trade history. Settings can be modified from the dashboard and persist across restarts via `data/saved_settings.json`.

## Hot Reload

`bot.py` watches all Python files for changes via file hash comparison. Modified files are auto-reloaded at the start of each scan cycle — no restart needed for most code changes. Exception: changes to bot.py itself or config.py require a restart.

## Troubleshooting

**scikit-learn/joblib showing as "not installed"**: Use `python3 -m pip install scikit-learn joblib` (not just `pip3 install`) to ensure packages install to the same Python that runs the bot.

**yfinance cross-symbol data contamination**: This is a known yfinance bug (GitHub #2557). The system uses ProcessPoolExecutor (not threads) to work around it. If you see wrong data for symbols, restart the bot.

**IBKR "peer closed connection"**: Transient TWS disconnection. The bot auto-reconnects. If persistent, restart TWS.

**Daily loss limit hit at $0.00**: Happens when IBKR reports $0 portfolio on first connection. Restart the bot after TWS has fully loaded.

**Reddit fetch 500 errors**: Transient Reddit server errors. The system retries automatically. No action needed.

**TA-Lib import error**: Install the C library first: `brew install ta-lib`, then `python3 -m pip install TA-Lib`.
