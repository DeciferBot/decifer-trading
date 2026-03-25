# Decifer Trading — Configuration Guide

> Quick reference for every parameter in `config.py`. When you change a value, update this doc and the CHANGELOG in the same commit.
>
> **Current config snapshot**: 2026-03-25

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
| `claude_model` | claude-sonnet-4-6 | Latest Sonnet |
| `claude_max_tokens` | 800 | Per agent call |

## Risk Management

| Parameter | Value | What It Does |
|-----------|-------|-------------|
| `risk_pct_per_trade` | 4% | Capital at risk per trade |
| `max_positions` | 12 | Simultaneous position cap |
| `daily_loss_limit` | 6% | Bot halts for the day |
| `max_drawdown_alert` | 15% | Alert + pause |
| `min_cash_reserve` | 10% | Always held in cash |
| `max_single_position` | 15% | Max % in one position |
| `max_sector_exposure` | 40% | Sector diversification cap |
| `consecutive_loss_pause` | 5 | Losses before 2-hour pause |
| `max_portfolio_allocation` | 1.0 | 1.0 = full account |
| `starting_capital` | $1,000,000 | For P&L tracking |

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

| Parameter | Value | What It Does |
|-----------|-------|-------------|
| `min_score_to_trade` | 28 | Minimum score out of 50 |
| `high_conviction_score` | 38 | 1.5x size above this |
| `agents_required_to_agree` | 3 | Of 6 agents |

## Scan Intervals (minutes)

| Session | Interval | Time Window |
|---------|----------|-------------|
| Prime | 5 | 9:45am–11:30am & 2pm–3:55pm |
| Standard | 10 | 11:30am–2pm |
| Extended | 7 | 4am–9:30am & 4pm–8pm |
| Overnight | 60 | 8pm–4am |

## Technical Indicators

| Category | Parameter | Value |
|----------|-----------|-------|
| Trend | EMA fast/slow/trend | 9 / 21 / 50 |
| Momentum | RSI period | 14 |
| Timing | MACD fast/slow/signal | 12 / 26 / 9 |
| Volatility | ATR period | 14 |
| Volume | Surge multiplier | 1.5x |
| Squeeze | Keltner period/ATR/mult | 20 / 10 / 1.5 |
| Breakout | Donchian period | 20 |

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
| `options_min_score` | 35 | Min stock score |
| `options_max_ivr` | 65 | IV Rank ceiling |
| `options_target_delta` | 0.50 | ATM targeting |
| `options_delta_range` | 0.20 | Acceptable window |
| `options_min_dte` | 5 | Min days to expiry |
| `options_max_dte` | 45 | Max days to expiry |
| `options_min_volume` | 50 | Daily volume floor |
| `options_min_oi` | 200 | Open interest floor |
| `options_max_spread_pct` | 25% | Bid-ask spread cap |
| `options_max_risk_pct` | 2.5% | Portfolio risk per trade |
| `options_profit_target` | 100% | Premium gain target |
| `options_stop_loss` | 50% | Premium loss stop |
| `options_exit_dte` | 2 | Hard exit DTE |
