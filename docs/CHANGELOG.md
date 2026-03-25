# Decifer Trading — Changelog

All notable changes to the Decifer trading system are documented here.
Format: newest entries at the top. Each entry includes the date, what changed, and why.

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
