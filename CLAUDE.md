# Decifer Trading — Session Context Brief
# Auto-loaded at every session start. Keep this current.

---

## North Star

Decifer is an autonomous paper-trading system that uses a 9-dimension signal engine and a 6-agent Claude AI pipeline to scan, score, and execute trades on IBKR (paper account DUP481326). The goal: generate high-quality training data across market regimes to eventually validate a live system.

**We are not building a live trading system yet. Every paper trade is a data point.**

**The only objective of this project is building alpha. Every feature, fix, and decision must serve that objective. If it does not directly contribute to generating, measuring, or preserving alpha, it should not be built.**

**No assumptions allowed. If something is unclear — about data, behavior, intent, or architecture — stop and ask Amit. Never fill gaps with guesses. Verify before building.**

Three actors:
| Actor | Role |
|-------|------|
| **Amit** | Decision maker, domain expert, final approver |
| **Cowork (Claude)** | Writes code, runs research, builds features |
| **Chief Decifer** | Read-only dashboard (port 8181). Never writes code. |

---

## Current State (update this when phases change)

- **Phase A — Complete ✅** (shipped 2026-03-28): Direction-agnostic signals, short-candidate scanner, directional skew tracking, consensus threshold raised to 3/6, mean-reversion dimension (9th signal)
- **IC scoring — Active**: Information Coefficient tracking is running. Gate for Phase C = 200 closed trades.
- **Phase B / C / D — Not yet built**: Signal validation (Alphalens), HMM regime detection, walk-forward weight calibration. All blocked on trade data volume.
- **Test suite**: ~60% pass rate. Tests and code diverged during rapid development. Runtime is unaffected — do not spend sessions fixing tests unless directly related to the work.
- **Regime detector**: VIX-proxy + SPY EMA (locked). HMM explicitly deferred until ≥200 closed trades.

---

## Architectural Decisions — The "Why" (read before touching anything)

These decisions are LOCKED. Do not second-guess them without reading `docs/DECISIONS.md` first and flagging Amit.

### Signal Engine: 9 Independent Dimensions, Not Overlapping Oscillators
RSI + Stochastic + CCI all measure momentum — using all three is one signal dressed up as three. Each of Decifer's 9 dimensions (Trend, Momentum, Squeeze, Flow, Breakout, Confluence, News, Social, Reversion) measures something fundamentally different. Adding a 10th dimension requires the same standard: it must be orthogonal to the existing 9.

### Direction-Agnostic Scoring, Not Regime-Switched Agent Prompts
We do not tell agents "you're in a bear market, be more bearish." That replaces bullish groupthink with regime-driven groupthink — one bad regime call cascades through all 6 agents. Instead, the signal engine scores setup *conviction* independently of direction. Bearish setups score identically to equivalent bullish setups. The market determines the long/short ratio naturally.

### Regime Detection: VIX-Proxy Locked, HMM Deferred
Hard classifier (BULL_TRENDING / BEAR_TRENDING / CHOPPY / PANIC) via VIX levels + SPY EMA. HMM is NOT running in production — `PRODUCTION_LOCKED = True`. Gate to reopen HMM: ≥200 closed trades AND IC Phase 2 review complete. Running two regime detectors in parallel is architecturally incoherent. HMM replaces VIX-proxy entirely when the gate is met, does not run alongside it.

### Skew Tracking: Diagnostic Only, Never a Feedback Loop
`get_directional_skew()` in `learning.py` tracks % long vs short. This is a dashboard metric and alert for Amit — it is NOT fed back into agent prompts. Feeding skew back ("you've been 80% long, correct") creates forced trades to balance a statistic. The market is structurally long-biased. Fighting that base rate is wrong.

### 6-Agent Pipeline: Devil's Advocate Is Mandatory
The Devil's Advocate agent exists specifically to counterbalance confirmation bias. The Risk Manager has hardcoded veto power — no agent can override risk limits. Paper threshold = 3/6 agents agree (aggressive for data generation). Live threshold = 4/6 (conservative).

### Paper Config: Aggressive for Data Generation
Paper trading thresholds are deliberately loose (min_score 18, agents_required 3, max_positions 20). Cost of a bad paper trade = zero. Value = training data. Every parameter that differs from live config is preserved as an inline comment in `config.py`. When switching to live, revert ALL of them.

### ProcessPoolExecutor, Not ThreadPoolExecutor
yfinance is not thread-safe (GitHub issue #2557). Concurrent threads share a global `_DFS` dict causing cross-symbol data contamination. The fix uses separate processes (each gets its own Python globals). Do not use `ThreadPoolExecutor` for `score_universe()` — ever.

### REVERSION Dimension: ADF Gate Is Non-Negotiable
The ADF test (p < 0.05) is the safety gate for mean-reversion scoring. Without it, 32% of random walks score positive on VR/OU/Z-score metrics. If ADF p ≥ 0.05, REVERSION scores 0 — no exceptions.

### Inverse ETFs, Not Direct Short Selling
Bearish exposure uses SPXS, SQQQ, UVXY. No borrow costs, no margin complications. Tracking error on leveraged products is acceptable for short-duration trades.

### Options: ATM Delta 0.50 Targeting
ATM options provide maximum leverage per dollar of premium. Better probability and more responsive Greeks than the common 0.30-0.40 delta targeting.

### News Sentinel: 3-Agent Pipeline, Not 6
Speed matters for breaking news (15-30 second window). Full 6-agent pipeline takes 5-10 minutes. Sentinel uses Catalyst Analyst + Risk Gate + Instant Decision. Position sizing is 0.75× to compensate for lighter analysis. Hardcoded risk limits still apply.

### Smart Execution: $10K / 500-Share Threshold
TWAP/VWAP/Iceberg only for orders above $10K notional or 500 shares. Smaller orders use simple limit orders. Smart execution adds latency — for small orders the market impact is negligible.

---

## Data Source Priority (always check this order)

1. **Alpaca Algo Trader Plus** (PRIMARY — paid, active): real-time quotes, historical bars, streaming, options Greeks. Use first for ALL market data.
2. **Alpha Vantage** (paid, active): earnings calendar, fundamentals, macroeconomic data.
3. **IBKR TWS**: execution and order management. Historical data only when Alpaca is insufficient.
4. **yfinance**: daily bars and index data, fallback only — never preferred over Alpaca.
5. TradingView Screener, Yahoo RSS, Finviz — supplementary screening and news.

---

## What NOT to Build Without a Gate

| Deferred Feature | Gate Condition |
|-----------------|----------------|
| HMM Regime Detection | ≥200 closed trades + IC Phase 2 review |
| Walk-Forward Weight Calibration | HMM + Alphalens both complete |
| Signal Validation (Alphalens) | ≥200 trades across regimes |
| ML Engine activation | ≥50 closed trades (already gated in `ml_engine.py`) |

---

## Key Files

| File | Purpose |
|------|---------|
| `docs/DECISIONS.md` | Full decision log with reasoning — read before changing architecture |
| `docs/PRODUCT_DEFINITION.md` | Authoritative state of what's actually built and running |
| `ARCHITECTURE.md` | System overview and development workflow |
| `roadmap/README.md` | Feature pipeline with dependency graph |
| `roadmap/` | Individual feature specs |
| `chief-decifer/state/` | Data contracts (sessions, research, specs) — path is sacred |
| `config.py` | All thresholds — live values preserved as inline comments |

---

## Session Protocol (mandatory)

1. **LOAD CONTEXT** — read checkpoint, last 2 session logs, active specs. If a `pending-doc-update.json` warning was injected, handle it first.
2. **REVIEW PENDING** — confirm branch, what feature is in flight
3. **COMMIT TO MASTER** — push directly to master unless Tier 3 multi-session rewrite
4. **TEST** — run relevant tests before declaring done
5. **UPDATE DOCS** — before committing, always ask: did the phase change? Did a new decision get locked? If yes:
   - Update "Current State" section in this file (CLAUDE.md)
   - Add the decision + reasoning to `docs/DECISIONS.md`
   - Update `memory/project_decifer.md` if phase or gates changed
   - The Stop hook will catch misses and prompt you automatically
6. **DRAFT SUMMARY** — write session log for Amit to approve before committing
7. **COMMIT & PUSH** — only after Amit approves

---

## Governance Rules

### Complexity Tiers
- **Tier 1** — Fast (read/check/scan): no approval needed
- **Tier 2** — Standard (implement/fix): proceed, document
- **Tier 3** — Deep (multi-file refactor, new phase planning): require Amit approval of approach BEFORE any code

### Architecture Integrity (paramount)
- Build from first principles, not patches. A patch that makes broken code "work" hides structural problems and accumulates debt.
- If a request conflicts with the architecture or vision, flag it to Amit before proceeding — never work around it silently.
- Functions > 30 lines are doing more than one thing. Modules > 200 lines have grown beyond scope. Stop and split.
- Every module has one clearly defined responsibility. If you cannot state it in one sentence, it's doing too much.

### Before Any Implementation
1. Does this belong in the existing architecture, or does it require a design decision first?
2. Am I patching something broken, or building something correct?
3. Does this change sustain or erode the long-term vision?

### Code Integrity
- Never invent function names, method signatures, or API behaviours without reading the actual source first.
- Any change touching signal generation, scoring, filtering, position sizing, or order submission — trace the full path from signal origin to order execution before committing.

### Hard Limits
- Paper account only: IBKR paper (DUP...). No live order submission.
- No secrets, credentials, or .env content in any commit.
- Never run `git reset --hard`, `git push --force`, or `git clean -f` without explicit Amit instruction.
- Pre-existing errors in touched files must be fixed in the same session, not silently worked around.

### Data Contracts (paths are sacred — do not change)
Chief has **one** state directory — `chief-decifer/state/`. No fallback. No split-brain.
The session-start hook reads from this path; Chief's panels read from this path; Cowork writes here.

| Data Type | Path | Written by | Read by |
|-----------|------|-----------|---------|
| Session logs | `chief-decifer/state/sessions/` | Cowork | Chief Decifer, session-start hook |
| Research | `chief-decifer/state/research/` | Cowork, `researcher.py` | Chief Decifer, session-start hook |
| Feature specs | `chief-decifer/state/specs/` | Cowork | Chief Decifer, session-start hook |
| Backlog | `chief-decifer/state/backlog.json` | Cowork | Chief Decifer, session-start hook |
| Vision | `chief-decifer/state/vision.json` | Amit | Chief Decifer, Cowork |
| Archived | `chief-decifer/state/archive/` | Cowork (on supersession) | humans only |
| Chief-internal | `chief-decifer/state/internal/` | Chief's own jobs | Chief Decifer only |

**Rule:** `research-*.json` belongs in `research/`, never in `specs/`. Specs describe
feature intent or completed work; research files are knowledge-base entries.

### Commit Format
```
<type>(<scope>): <short description>

<body — what changed and why, 2-3 sentences>

Approved-by: Amit
```
Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`

---

---

## New Machine Setup

**If the environment looks unconfigured (missing packages, no `.env`, empty state dirs), run setup before doing anything else:**

```bash
cd "/path/to/decifer trading"
bash scripts/setup.sh
```

The script handles everything automatically:
- Installs Homebrew, `python@3.11`, `ta-lib`, `uv`, and other system deps
- Clones or pulls the repo
- Installs all Python packages from both `requirements.txt` and `Chief-Decifer-recovered/requirements.txt` via `uv` (no manual pip install needed)
- Restores `.env` from iCloud Keychain or iCloud Drive backup
- Installs NLTK data, launch daemons, etc.

**If `.env` is missing after setup** (no iCloud backup on new machine):
1. Copy the template: `cp .env.example .env`
2. Fill in all 9 keys: `ANTHROPIC_API_KEY`, `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_BASE_URL`, `FMP_API_KEY`, `ALPHA_VANTAGE_KEY`, `IBKR_ACTIVE_ACCOUNT`, `IBKR_PAPER_ACCOUNT`, `FRED_API_KEY`

**Signs of an unconfigured environment to watch for:**
- `ModuleNotFoundError` on import → run `bash scripts/setup.sh`
- `ANTHROPIC_API_KEY` empty → `.env` not loaded; check root `.env` exists
- Signal scripts writing to wrong paths → `config.py` auto-detects repo root via `__file__`, no `DECIFER_REPO_PATH` needed

---

*This file is the primary session context. Update "Current State" when phases change or new decisions are locked. Full reasoning lives in `docs/DECISIONS.md`.*
