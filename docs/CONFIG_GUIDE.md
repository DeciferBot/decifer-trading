# Decifer Trading — Configuration Guide

> Quick reference for every parameter in `config.py`. When you change a value, update this doc and the CHANGELOG in the same commit.
>
> **Current config snapshot**: 2026-03-26
> **Mode**: PAPER TRADING (data generation) — live values in parentheses

---

## IBKR Connection

| Parameter | Value | Notes |
|-----------|-------|-------|
| `ibkr_host` | 127.0.0.1 | Local TWS/Gateway |
| `ibkr_port` | 7496 | TWS/Gateway port |
| `ibkr_client_id` | 10 | Unique per connection |
| `active_account` | DUP481326 | **PAPER** account — change to U3059777 or U24093086 for live |

## Accounts

| Alias | Account ID | Purpose |
|-------|-----------|---------|
| paper | DUP481326 | Paper trading / testing |
| live_1 | U3059777 | Live account 1 |
| live_2 | U24093086 | Live account 2 |

## AI Brain

| Parameter | Value | Notes |
|-----------|-------|-------|
| `anthropic_api_key` | (from .env) | Set via ANTHROPIC_API_KEY environment variable. Never hardcode |
| `claude_model` | claude-sonnet-4-6 | Latest Sonnet |
| `claude_max_tokens` | 800 | Per agent call |

## Risk Management

> **NOTE**: Values tuned for paper trading data generation. Live values in parentheses.

| Parameter | Paper Value | Live Value | What It Does |
|-----------|-------------|------------|-------------|
| `risk_pct_per_trade` | 3% | 4% | Capital at risk per trade |
| `max_positions` | 20 | 12 | Simultaneous position cap |
| `daily_loss_limit` | 10% | 6% | Bot halts for the day |
| `max_drawdown_alert` | 25% | 15% | Alert + pause |
| `min_cash_reserve` | 5% | 10% | Always held in cash |
| `max_single_position` | 10% | 15% | Max % in one position |
| `max_sector_exposure` | 50% | 40% | Sector diversification cap |
| `consecutive_loss_pause` | 8 | 5 | Losses before 2-hour pause |
| `max_portfolio_allocation` | 1.0 | 1.0 | 1.0 = full account |
| `starting_capital` | $1,000,000 | $1,000,000 | For P&L tracking |

## Exit Rules

| Parameter | Value | What It Does |
|-----------|-------|-------------|
| `atr_stop_multiplier` | 1.5 | Stop = entry − 1.5×ATR |
| `atr_trail_multiplier` | 2.0 | Trailing stop = 2×ATR from high |
| `partial_exit_1_pct` | 4% | Sell 33% here |
| `partial_exit_2_pct` | 8% | Sell 33% here, trail rest |
| `min_reward_risk_ratio` | 1.5 | Minimum R:R to enter |
| `gap_protection_pct` | 3% | Exit on adverse gap |

## Scoring

| Parameter | Paper Value | Live Value | What It Does |
|-----------|-------------|------------|-------------|
| `min_score_to_trade` | 18 | 28 | Minimum score out of 50 |
| `high_conviction_score` | 30 | 38 | 1.5x size above this |
| `agents_required_to_agree` | 2 | 4 | Of 6 agents |

> **Regime-specific thresholds** in `signals.py` are now dynamically derived from `min_score_to_trade`. Paper config (18) automatically lowers all regime gates proportionally.

## Scan Intervals (minutes)

| Session | Paper | Live | Time Window |
|---------|-------|------|-------------|
| Prime | 3 | 5 | 9:45am–11:30am & 2pm–3:55pm |
| Standard | 5 | 10 | 11:30am–2pm |
| Extended | 5 | 7 | 4am–9:30am & 4pm–8pm |
| Overnight | 30 | 60 | 8pm–4am |

## Technical Indicators

| Category | Config Key(s) | Value | Notes |
|----------|---------------|-------|-------|
| Trend | `ema_fast`, `ema_slow`, `ema_trend` | 9 / 21 / 50 | EMA periods for trend alignment |
| Momentum | `rsi_period` | 14 | RSI lookback period |
| Timing | `macd_fast`, `macd_slow`, `macd_signal` | 12 / 26 / 9 | MACD parameters |
| Volatility | `atr_period` | 14 | ATR lookback period |
| Volume | `volume_surge_multiplier` | 1.5 | Volume must be 1.5x average |
| Squeeze | `keltner_period`, `keltner_atr_period`, `keltner_multiplier` | 20 / 10 / 1.5 | Keltner channel for squeeze detection |
| Breakout | `donchian_period` | 20 | Donchian channel lookback |

## VIX Regime

| Parameter | Value | Regime |
|-----------|-------|--------|
| `vix_bull_max` | 15 | Below = bull |
| `vix_choppy_max` | 25 | 15–25 = choppy |
| `vix_panic_min` | 35 | Above = no trades |
| `vix_spike_pct` | 20% | Spike = exit all |

## Options

| Parameter | Value | Notes |
|-----------|-------|-------|
| `options_enabled` | True | Active |
| `options_min_score` | 35 | Min stock score to consider options |
| `options_max_ivr` | 65 | IV Rank ceiling (< 30 ideal, 30-50 fair, 50-65 acceptable) |
| `options_target_delta` | 0.50 | ATM targeting |
| `options_delta_range` | 0.35 | Acceptable window either side (0.15-0.85 range) |
| `options_min_dte` | 5 | Min days to expiry |
| `options_max_dte` | 45 | Max days to expiry |
| `options_min_volume` | 25 | Daily contracts traded (relaxed for mid-cap) |
| `options_min_oi` | 100 | Open interest floor (relaxed for mid-cap) |
| `options_max_spread_pct` | 35% | Bid-ask spread cap (relaxed for small/mid-cap) |
| `options_max_risk_pct` | 2.5% | Portfolio risk per trade (premium = max loss) |
| `options_profit_target` | 100% | Premium gain target |
| `options_stop_loss` | 50% | Premium loss stop |
| `options_exit_dte` | 2 | Hard exit DTE (gamma risk) |

## News Sentinel

The sentinel runs as an independent background thread, monitoring news in real-time and firing a 3-agent mini pipeline when material news is detected.

| Parameter | Value | What It Does |
|-----------|-------|-------------|
| `sentinel_enabled` | True | Master on/off switch |
| `sentinel_poll_seconds` | 45 | Seconds between news polls (30–60 recommended) |
| `sentinel_cooldown_minutes` | 10 | Don't re-trigger the same symbol within this window |
| `sentinel_batch_size` | 10 | Symbols scanned per poll cycle (rotates through universe) |
| `sentinel_max_symbols` | 80 | Maximum symbols in sentinel monitoring universe |
| `sentinel_keyword_threshold` | 3 | Minimum \|keyword_score\| to consider news material |
| `sentinel_claude_confidence` | 7 | Claude confidence ≥ this auto-upgrades urgency |
| `sentinel_min_confidence` | 5 | Minimum decision confidence (0–10) to execute a trade |
| `sentinel_use_ibkr` | True | Use IBKR news API (Benzinga, DowJones, etc.) |
| `sentinel_use_finviz` | True | Use Finviz news scraping |
| `sentinel_risk_multiplier` | 0.75 | Position size multiplier for sentinel trades (smaller = safer) |
| `sentinel_max_trades_per_hour` | 3 | Rate limit on sentinel-driven trades |

### Tuning Tips

- **More aggressive**: Lower `sentinel_keyword_threshold` to 2, lower `sentinel_min_confidence` to 4, raise `sentinel_max_trades_per_hour` to 5
- **More conservative**: Raise `sentinel_keyword_threshold` to 5, raise `sentinel_min_confidence` to 7, lower `sentinel_risk_multiplier` to 0.5
- **Disable without restarting**: Set `sentinel_enabled: False` via dashboard settings
- **Reduce API costs**: Set `sentinel_use_ibkr: False` to skip IBKR news calls, or increase `sentinel_poll_seconds` to 90

## ML Engine (scikit-learn Learning Loop)

Learns from trade history to identify winning patterns and enhance signal scores. Requires `scikit-learn` and `joblib` (installed via requirements.txt). Has graceful fallback if packages are missing.

| Parameter | Value | What It Does |
|-----------|-------|-------------|
| `ml_enabled` | True | Master switch: enable ML score enhancements |
| `ml_min_trades` | 50 | Minimum completed trades before ML training starts |
| `ml_retrain_interval` | 168 | Hours between automatic retraining (168 = 1 week) |
| `ml_confidence_weight` | 0.3 | ML adjustment weight: 0.3 = 30% influence on final score |
| `ml_models_dir` | data/models | Directory for persisted .pkl model files |

The ML engine uses RandomForest (classifier) + GradientBoosting (regressor) with walk-forward cross-validation via `TimeSeriesSplit`. It multiplies signal scores by 0.5x-1.5x based on predicted trade outcome. Models are saved to `data/models/` as `.pkl` files via `joblib`.

### ML Pipeline Flow

1. TradeLabeler reads `data/trades.json` and labels each trade win/loss
2. Feature matrix built from signal scores, regime, indicators
3. Models trained with TimeSeriesSplit cross-validation (no lookahead bias)
4. SignalEnhancer adjusts live scores by 0.5x-1.5x based on ML confidence
5. Automatic retraining every `ml_retrain_interval` hours

## Social Sentiment (Reddit + ApeWisdom + VADER)

Tracks social media mention velocity and sentiment for stock symbols. Runs as a background polling thread (60-second interval). No API key required.

| Component | Source | What It Does |
|-----------|--------|-------------|
| Reddit JSON API | r/wallstreetbets, r/stocks, r/investing | Scrapes posts/comments for ticker mentions |
| ApeWisdom | apewisdom.io | Aggregated Reddit mention counts |
| VADER | NLTK library | Sentiment analysis with 90-word custom finance lexicon |

The social sentiment score (0-10) feeds into dimension #8 of the signal engine. It tracks mention **velocity** (acceleration of mentions over time), not raw count. A stock going from 5 mentions/hour to 50 mentions/hour scores higher than one with a steady 100 mentions/hour.

Requires `nltk` package and VADER lexicon download: `python3 -c "import nltk; nltk.download('vader_lexicon')"`

## Data Collector (Historical ML Training Data)

Downloads and stores historical OHLCV data for ML model training. Runs automatically as a daemon thread on bot startup, and can also be run manually via CLI.

| Parameter | Value | Notes |
|-----------|-------|-------|
| Default symbols | 61 | Covers FAANG+, semis, financials, healthcare, energy, consumer, ETFs, meme stocks |
| Intraday data | yfinance 5m bars | 60-day rolling window per symbol |
| Daily data | yfinance + Stooq backup | Full history (20+ years for most symbols) |
| Storage format | Parquet (pyarrow) | Columnar, compressed, fast to read |
| Pre-computed features | 12+ | Returns, ATR, EMAs, RSI, MFI, BB position, VWAP distance, volume ratio, regime |

## Dashboard

| Parameter | Value | Notes |
|-----------|-------|-------|
| `dashboard_port` | 8080 | Web dashboard at http://localhost:8080 |

## Logging

| Parameter | Value | Notes |
|-----------|-------|-------|
| `log_file` | logs/decifer.log | Main bot log (all modules) |
| `trade_log` | data/trades.json | Every trade entry/exit with P&L |
| `order_log` | data/orders.json | Every IBKR order submitted |

## Market Hours (EST)

| Parameter | Value | Notes |
|-----------|-------|-------|
| `pre_market_start` | 04:00 | Extended hours begin |
| `market_open` | 09:30 | Regular session open |
| `prime_start` | 09:45 | Avoids first 15 min volatility |
| `lunch_start` | 11:30 | Lower-frequency scanning begins |
| `afternoon_start` | 14:00 | Prime scanning resumes |
| `close_buffer` | 15:55 | Avoids last 5 min |
| `market_close` | 16:00 | Regular session close |
| `after_hours_end` | 20:00 | Extended hours end |

## Inverse ETFs

Used during elevated/panic VIX regimes for short exposure:

| Alias | Symbol | Purpose |
|-------|--------|---------|
| market_short | SPXS | 3x inverse S&P 500 |
| tech_short | SQQQ | 3x inverse Nasdaq |
| vix_long | UVXY | VIX spike play |
