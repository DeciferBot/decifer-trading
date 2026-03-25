# Decifer Trading — Strategy & Logic Reference

> This document tracks the **current** trading logic. When we change how Decifer makes decisions, this doc gets updated in the same commit. For the full history, use `git log docs/STRATEGY.md`.

---

## Agent Pipeline

Decifer runs **6 Claude agents** sequentially on every scan cycle. A trade requires agreement from at least **3 of 6** agents (configurable via `agents_required_to_agree`).

| # | Agent | Role | Can Veto? |
|---|-------|------|-----------|
| 1 | Technical Analyst | Price action, volume, indicators across 5m / 1D / 1W | No |
| 2 | Macro Analyst | Regime classification, VIX, cross-asset dynamics, news | No |
| 3 | Opportunity Finder | Synthesises technical + macro + options flow → top 3 trades | No |
| 4 | Devil's Advocate | Argues against every proposed trade | No |
| 5 | Risk Manager | Position sizing, portfolio-level risk checks | **Yes** |
| 6 | Final Decision Maker | Outputs executable JSON trade instructions | No |

## Signal Dimensions (signals.py)

Each dimension measures something independent — no redundant oscillators.

| Dimension | Indicator | What It Measures |
|-----------|-----------|-----------------|
| **Trend** | EMA alignment (9/21/50) + ADX strength | Direction and strength of trend |
| **Momentum** | MFI (volume-weighted RSI) | Buying/selling pressure with volume confirmation |
| **Squeeze** | Bollinger Bands inside Keltner Channel | Volatility compression (coiled spring) |
| **Flow** | VWAP position + OBV divergence | Institutional money flow |
| **Breakout** | Donchian channel breach + volume | Price range expansion with participation |
| **Confluence** | Multi-timeframe agreement | Same signal across 5m, 1D, 1W |

## Scoring

- **Score range**: 0–50
- **Minimum to trade**: 28 (`min_score_to_trade`)
- **High conviction**: 38+ (`high_conviction_score`) → 1.5x position size

## Risk Rules (Hardcoded — Agent-Proof)

These rules cannot be overridden by any agent:

| Rule | Value | Effect |
|------|-------|--------|
| Risk per trade | 4% | Max capital at risk in any single position |
| Max positions | 12 | Hard cap on simultaneous open positions |
| Daily loss limit | 6% | Bot halts for the day |
| Max drawdown alert | 15% | Alert + pause trading |
| Cash reserve | 10% | Always held in cash |
| Max single position | 15% | No position exceeds this % of portfolio |
| Max sector exposure | 40% | Diversification enforcement |
| Consecutive loss pause | 5 losses | 2-hour trading pause |
| Min reward:risk | 1.5:1 | Below this = skip the trade |

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

| Session | Interval | Time (EST) |
|---------|----------|------------|
| Prime | 5 min | 9:45am–11:30am, 2pm–3:55pm |
| Standard | 10 min | 11:30am–2pm (lunch lull) |
| Extended | 7 min | 4am–9:30am pre-market, 4pm–8pm after-hours |
| Overnight | 60 min | 8pm–4am (monitoring only) |
