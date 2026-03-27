# CLAUDE.md — Project Instructions for Decifer Trading

## Auto-Commit and Push Rule

**After completing ANY task — code changes, bug fixes, feature additions, upgrades, config changes, refactors, or even feature discussions that result in file changes — ALWAYS commit and push to the repo.**

Workflow:
1. Stage only the relevant changed files (no `git add -A` unless appropriate)
2. Write a clear, concise commit message describing what was done and why
3. Push to the current branch on origin
4. Confirm the push succeeded

This applies to ALL work, no exceptions. Do not ask "should I commit this?" — just do it.

## Project Context

- Repo: DeciferBot/decifer-trading (private)
- Remote: origin → https://github.com/DeciferBot/decifer-trading.git
- IBKR paper account only (not live trading)
- Free data stack: yfinance, TV Screener, Yahoo RSS, Finviz
