# Decifer Trading 2.0 — Autonomous Trading System

AI-powered autonomous trading bot built on Claude + Interactive Brokers. Uses a 6-agent multi-perspective intelligence system to scan, analyse, and execute trades across stocks and options.

## Architecture

The system runs a pipeline of 6 specialised Claude agents on every scan cycle:

1. **Technical Analyst** — price action, volume, indicators across 5m / 1D / 1W timeframes
2. **Macro Analyst** — regime classification, VIX, cross-asset dynamics, news flow
3. **Opportunity Finder** — synthesises technical + macro + options flow into top 3 trades
4. **Devil's Advocate** — argues against every proposed trade
5. **Risk Manager** — veto power, position sizing, portfolio-level risk checks
6. **Final Decision Maker** — outputs executable JSON trade instructions

A trade requires agreement from at least 4 of 6 agents (configurable).

## Workspace Structure

```
decifer trading/
├── bot.py               # Main entry point — scan loop, execution
├── config.py            # All settings in one place
├── agents.py            # 6-agent Claude pipeline
├── signals.py           # Technical indicators (EMA, RSI, MACD, ATR + TA-Lib)
├── scanner.py           # Dynamic universe via TradingView Screener
├── options.py           # Options chain analysis, IV rank, Greeks
├── options_scanner.py   # Proactive options flow scanner
├── risk.py              # 5-layer risk management (hardcoded, agent-proof)
├── orders.py            # IBKR order execution (limit, OCO brackets, options)
├── learning.py          # Trade logging, performance tracking, weekly review
├── dashboard.py         # Live web dashboard (port 8080)
├── patch.py             # Hot-patch utility (no restart needed)
├── requirements.txt     # Python dependencies
├── launch_decifer.command  # macOS launch script
├── .env                 # API keys (never commit)
├── .gitignore
│
├── data/
│   ├── trades.json          # Trade history log
│   ├── equity_history.json  # Equity curve data
│   └── favourites.json      # Watchlist
├── docs/
│   ├── Decifer_Architecture.docx
│   ├── Decifer_v3_Architecture.docx
│   ├── Decifer_v3_Documentation.docx
│   └── Decifer_Product_Document.docx
├── logs/
│   └── decifer.log
├── configs/             # Additional config files
├── notebooks/           # Jupyter research notebooks
├── strategies/          # Strategy development & backtesting
│   ├── live/
│   ├── backtest/
│   └── archive/
├── scripts/             # Utility scripts
└── tests/               # Unit & integration tests
```

## Quick Start

```bash
# 1. Add your Anthropic API key to .env
#    Edit .env and replace YOUR_API_KEY_HERE

# 2. Make sure TWS or IB Gateway is running (paper trading: port 7497)

# 3. Launch
chmod +x launch_decifer.command
./launch_decifer.command

# Or manually:
pip3 install -r requirements.txt
python3 bot.py
```

## Key Configuration (config.py)

- **IBKR Connection**: port 7496 (TWS), paper account `DUP481326`
- **Risk**: 2% per trade, max 6 positions, 4% daily loss limit, 40% cash reserve
- **Scoring**: min 35/50 to trade, 45+ = high conviction (1.5x size)
- **Options**: enabled, target delta 0.40, 7–21 DTE, IVR < 50
- **Agents required**: 4 of 6 must agree

## Dashboard

Live at `http://localhost:8080` — shows positions, agent reasoning, P&L, regime, and scan status.
