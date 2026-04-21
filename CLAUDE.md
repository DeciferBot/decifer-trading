# Decifer Trading — Session Context Brief
# Auto-loaded at every session start. Keep this current.

---

## North Star

Decifer is an autonomous paper-trading system that uses a 10-dimension signal engine and a 4-agent Claude AI pipeline to scan, score, and execute trades on IBKR (paper account DUP481326). The goal: generate high-quality training data across market regimes to eventually validate a live system.

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

- **Phase A — Complete ✅** (shipped 2026-03-28): Direction-agnostic signals, short-candidate scanner, directional skew tracking, consensus threshold set to 3/4 agents, mean-reversion dimension (10th signal)
- **IC scoring — Active**: Information Coefficient tracking is running. Gate for Phase C = 200 closed trades.
- **PM ADD verb — Activated ✅** (2026-04-15): Portfolio Manager now shows Opus the full decision surface (entry thesis, per-dimension entry→current deltas with IC-weight annotations, setup type, pattern, regime, news) and lets Opus decide ADD/TRIM/EXIT/HOLD. Code (`calculate_position_size()`) sizes ADDs — same function entries use. Opus no longer emits `ADD_NOTIONAL`. Hardcoded safety floors: `check_risk_conditions`, earnings-48h, single-position-cap clamp (downgrades to HOLD if no headroom).
- **Three-tier universe — Active ✅**: TV Screener ripped out. Universe is now: committed universe (top-1000 by dollar volume, weekly refresh) + dynamic adds (catalyst hits, held positions, favourites, sympathy plays, news-driven). Scanner pulls from committed universe; dynamic tiers bypass the gate.
- **Catalyst screener — Active ✅**: `catalyst_engine.py` scores EDGAR filings, earnings surprises, and analyst actions in real-time. High-conviction catalyst hits get a flat score boost to clear `min_score_to_trade`. Wired into both the main signal engine and the Chief Decifer dashboard.
- **Phase B / C / D — Not yet built**: Signal validation (Alphalens), HMM regime detection, walk-forward weight calibration. All blocked on trade data volume.
- **Test suite**: 1704/1705 passing (2026-04-16). Tests are current with the codebase.
- **Regime detector**: VIX-proxy + SPY EMA (locked). HMM explicitly deferred until ≥200 closed trades.

---

## Architectural Decisions — The "Why" (read before touching anything)

These decisions are LOCKED. Do not second-guess them without reading `docs/DECISIONS.md` first and flagging Amit.

### Signal Engine: 10 Independent Dimensions, Not Overlapping Oscillators
RSI + Stochastic + CCI all measure momentum — using all three is one signal dressed up as three. Each of Decifer's 10 dimensions (Directional, Momentum, Squeeze, Flow, Breakout, PEAD, News, Short Squeeze, Reversion, Overnight Drift) measures something fundamentally different. Two optional dimensions (Social, IV Skew) are config-gated. Adding a new dimension requires the same standard: it must be orthogonal to the existing ones.

### Direction-Agnostic Scoring, Not Regime-Switched Agent Prompts
We do not tell agents "you're in a bear market, be more bearish." That replaces bullish groupthink with regime-driven groupthink — one bad regime call cascades through all agents. Instead, the signal engine scores setup *conviction* independently of direction. Bearish setups score identically to equivalent bullish setups. The market determines the long/short ratio naturally.

### Regime Detection: VIX-Proxy Locked, HMM Deferred
Hard classifier (BULL_TRENDING / BEAR_TRENDING / CHOPPY / PANIC) via VIX levels + SPY EMA. HMM is NOT running in production — `PRODUCTION_LOCKED = True`. Gate to reopen HMM: ≥200 closed trades AND IC Phase 2 review complete. Running two regime detectors in parallel is architecturally incoherent. HMM replaces VIX-proxy entirely when the gate is met, does not run alongside it.

### Skew Tracking: Diagnostic Only, Never a Feedback Loop
`get_directional_skew()` in `learning.py` tracks % long vs short. This is a dashboard metric and alert for Amit — it is NOT fed back into agent prompts. Feeding skew back ("you've been 80% long, correct") creates forced trades to balance a statistic. The market is structurally long-biased. Fighting that base rate is wrong.

### 4-Agent Pipeline: Risk Manager Has Veto Power
The pipeline is: Technical Analyst (deterministic) + Trading Analyst (Opus, 1 LLM call) + Risk Manager (deterministic, hardcoded veto) + Final Decision Maker (deterministic). Devil's Advocate was removed — the Trading Analyst sees all data simultaneously, eliminating the anchoring bias the DA was meant to counter. Paper threshold = 3/4 agents agree (aggressive for data generation). Live threshold = 4/4 (conservative).

### Paper Config: Aggressive for Data Generation
Paper trading thresholds are deliberately loose (min_score 14, agents_required 3, max_positions 100 sanity ceiling). Cost of a bad paper trade = zero. Value = training data. Every parameter that differs from live config is preserved as an inline comment in `config.py`. When switching to live, revert ALL of them (live: min_score 28, agents_required 4).

### ThreadPoolExecutor for score_universe()
`score_universe()` uses `ThreadPoolExecutor`. IBKR `reqHistoricalData` is thread-safe via a shared IB connection — the original yfinance thread-safety concern (GitHub issue #2557) no longer applies since Alpaca is the primary data source. Do not revert to ProcessPoolExecutor without verifying the data source in use.

### REVERSION Dimension: ADF Gate Is Non-Negotiable
The ADF test (p < 0.05) is the safety gate for mean-reversion scoring. Without it, 32% of random walks score positive on VR/OU/Z-score metrics. If ADF p ≥ 0.05, REVERSION scores 0 — no exceptions.

### Inverse ETFs, Not Direct Short Selling
Bearish exposure uses SPXS, SQQQ, UVXY. No borrow costs, no margin complications. Tracking error on leveraged products is acceptable for short-duration trades.

### Options: ATM Delta 0.50 Targeting
OTM options (δ 0.30–0.40) have higher leverage per dollar of premium — but ATM (δ 0.50) is the correct choice for this system for three reasons:
1. **Liquidity** — ATM options have the highest volume, tightest spreads, and most open interest. Fill quality matters more than theoretical leverage.
2. **Gamma/theta ratio** — ATM options have maximum gamma per unit of theta. OTM options at short DTE decay catastrophically fast and require a large move AND correct timing; ATM only requires directional correctness.
3. **Signal type** — Decifer's momentum/breakout signals fire when a stock is already moving. ATM captures that move immediately. OTM requires the move to exceed the strike before theta erodes the position.

### News Sentinel: 3-Agent Pipeline, Not 4
Speed matters for breaking news (15-30 second window). Full 4-agent pipeline takes 5-10 minutes. Sentinel uses Catalyst Analyst + Risk Gate + Instant Decision. Position sizing is 0.75× to compensate for lighter analysis. Hardcoded risk limits still apply.

### Smart Execution: $10K / 500-Share Threshold
TWAP/VWAP/Iceberg only for orders above $10K notional or 500 shares. Smaller orders use simple limit orders. Smart execution adds latency — for small orders the market impact is negligible.

---

## Data Source Priority (always check this order)

1. **Alpaca Algo Trader Plus** (PRIMARY for market data — paid, active): real-time quotes, historical bars, streaming, options Greeks. Use first for ALL price/volume/intraday data. **MCP is data-only — never use `mcp__alpaca__get_all_positions` or any Alpaca MCP position/order tool to check portfolio state. Positions and trades live in IBKR.**
2. **FMP — Financial Modeling Prep** (PRIMARY for fundamentals/events — paid premium, 750 calls/min, MCP server connected): analyst consensus, price targets, grade breakdowns, insider trades (Form 4), congressional trades (Senate/House), income statements, revenue growth, EPS acceleration, key metrics TTM, DCF valuations, earnings calendar, earnings estimates, short interest, shares float, sector performance, stock news, press releases, 30 years history. **Use FMP first for anything fundamental, event-driven, or analyst-related.** Client: `fmp_client.py`. MCP server: `fmp` (connected via `~/.claude.json`). **MCP is data-only — never use FMP MCP to infer portfolio or position state.**
3. **Alpha Vantage** (paid, active): macroeconomic indicators, economic calendar. Fallback for fundamentals if FMP is unavailable.
4. **IBKR TWS**: execution, order management, and **the source of truth for all portfolio positions and trade history**. To check current positions ask Amit to query TWS directly or read `data/trades.json`. Historical data only when Alpaca is insufficient.
5. **yfinance**: daily bars and index data, fallback only — never preferred over Alpaca or FMP.
6. Yahoo RSS, Finviz — supplementary news only. TradingView Screener was removed (replaced by three-tier committed universe).

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

0. **CHECK ENVIRONMENT** — before anything else, verify the machine is set up:
   - Run `python3 -c "import anthropic, pandas, dash"` — if this fails, run `bash scripts/setup.sh` immediately and stop until it completes.
   - Check that `.env` exists at the repo root — if missing, run `bash scripts/setup.sh` (it will pull all secrets from iCloud Keychain automatically).
   - Do not proceed with any task until the environment check passes.

1. **LOAD CONTEXT** — read checkpoint, last 2 session logs, active specs. If a `pending-doc-update.json` warning was injected, handle it first.
2. **REVIEW PENDING** — confirm branch, what feature is in flight
3. **COMMIT TO MASTER** — push directly to master unless Tier 3 multi-session rewrite
4. **TEST** — run relevant tests before declaring done
5. **UPDATE DOCS** — before committing, always ask: did the phase change? Did a new decision get locked? If yes:
   - Update "Current State" section in this file (CLAUDE.md)
   - Add the decision + reasoning to `docs/DECISIONS.md`
   - Update `memory/project_decifer.md` if phase or gates changed
   - The Stop hook will catch misses and prompt you automatically
6. **DRAFT SUMMARY** — write session log for Amit to approve before committing. Use this format every time:

```
DATE: [today]

WHAT CHANGED:
  - [file or feature]: [what was built/fixed and why]

WHAT WAS DELETED:
  - [file or function removed, or "nothing deleted"]

DECISIONS MADE:
  - [any locked architectural decision, or "none"]

TESTS:
  - [pass/fail count, or "tests not applicable"]

WHAT IS NEXT:
  - [next logical task, or "nothing — phase gate not met"]
```

7. **COMMIT & PUSH** — only after Amit approves

---

## Governance Rules

### Complexity Tiers
- **Tier 1** — Fast (read/check/scan): no approval needed
- **Tier 2** — Standard (implement/fix): proceed, document
- **Tier 3** — Deep (multi-file refactor, new phase planning): require Amit approval of approach BEFORE any code

### Architecture Integrity (paramount)

**PATCHES ARE COMPLETELY PROHIBITED. THIS IS A HARD RULE WITH NO EXCEPTIONS.**

A patch is any change that suppresses a symptom without addressing its root cause. This includes: `try/except` blocks added to silence errors, default fallback values that mask missing data, conditional branches added to "handle" an edge case that shouldn't exist, and any fix that makes a test pass without understanding why it was failing.

**The mandatory sequence before a single line of code is written:**
1. **STOP.** Do not open any file with intent to edit.
2. **DIAGNOSE.** Trace the failure to its actual origin — not the line that raised the error, but why that condition exists at all. Read every layer involved. Follow imports. Read callers. Read the data flow.
3. **ARTICULATE.** State the root cause in one clear sentence. If you cannot do this, you do not understand it yet — keep digging.
4. **RESEARCH.** Understand what the correct design looks like from first principles. What should this code do? Why did the original design fail to do it? What invariant was violated?
5. **ONLY THEN: implement.** Fix at the root. If the root cause requires a rewrite, do the rewrite. If it requires a design decision, bring it to Amit before writing a single line.

**Violations that will not be tolerated:**
- Catching an exception to prevent a crash without removing the condition that causes it
- Adding an `if x is None: return` guard without understanding why `x` is None
- Hardcoding a value to make output correct without understanding why the computed value is wrong
- Any change described as "temporary" or "for now"
- Adjusting a test to make it pass rather than fixing the code it tests

If a request conflicts with the architecture or vision, flag it to Amit before proceeding — never work around it silently.

Functions > 30 lines are doing more than one thing. Modules > 200 lines have grown beyond scope. Stop and split.

Every module has one clearly defined responsibility. If you cannot state it in one sentence, it's doing too much.

### Before Any Implementation
1. **What is the root cause — stated in one sentence?** If this cannot be answered, stop. Do not proceed.
2. Does this belong in the existing architecture, or does it require a design decision first?
3. Is this fix correct from first principles, or does it merely suppress a symptom?
4. Does this change sustain or erode the long-term vision?

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
