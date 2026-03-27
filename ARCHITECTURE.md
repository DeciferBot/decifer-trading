# Decifer Trading — Architecture & Workflow

**Last updated:** 2026-03-27
**Status:** Active — this document governs how all development on Decifer is done.

---

## System Overview

Decifer is an autonomous trading system built on Interactive Brokers (paper account), using Claude AI for signal analysis, with a free data stack (yfinance, TradingView Screener, Yahoo RSS, Finviz).

**Three actors in the development process:**

| Actor | Role | Tools |
|-------|------|-------|
| **Amit** | Decision maker, domain expert, reviewer | Claude Cowork, GitHub |
| **Cowork (Claude)** | Writes code, tests, does research, executes features | Bash, Edit, WebSearch, WebFetch |
| **Chief Decifer** | Monitors, visualizes, tracks progress (eyes only) | Dashboard on port 8181, reads repo state |

---

## Chief Decifer — Eyes Only Architecture

Chief Decifer is a **read-only monitoring layer**. It does NOT write code, generate tests, or run autonomous agent loops. Its job is to read state and display it on the dashboard.

### What Chief Does

- Displays test pass/fail results (runs `pytest`, reads output)
- Shows git history and recent changes
- Tracks feature lifecycle: researched → spec'd → building → built → tested
- Visualizes signal performance, backtest results, portfolio state
- Renders research findings from scheduled Cowork tasks
- Shows session summaries from Cowork development sessions

### What Chief Does NOT Do

- Write, edit, or generate any source code
- Generate test files
- Run autonomous coding/testing agent loops
- Perform "research" by asking an LLM to simulate web search results

### Agents Removed

- **Coder agent** — removed entirely
- **Tester agent** — removed entirely
- **Researcher agent** — replaced by scheduled Cowork task with real web search

### Agents Retained (modified)

- **Dashboard agent** — renders all panels, reads state files
- Any monitoring/reporting logic that reads and displays data

---

## Data Contracts — How Chief Gets Its Data

Chief reads from three folders. Cowork writes to them. Chief never writes to them.

### 1. Session Logs (`chief-decifer/state/sessions/`)

Written by Cowork at the end of each development session (after Amit approves the summary).

```json
{
  "session_id": "2026-03-27T14:30",
  "date": "2026-03-27",
  "work_items": [
    {
      "type": "bugfix|feature|refactor|test|docs",
      "component": "orders.py",
      "summary": "Fixed stale position cache after IBKR reconnect",
      "root_cause": "Position cache not invalidated on reconnect event",
      "attempts": 3,
      "resolution": "Added cache flush in reconnect handler",
      "files_changed": ["orders.py", "reconnect.py"],
      "tests_added": ["test_orders_reconnect.py"],
      "tests_passing": true
    }
  ],
  "git_commits": ["abc1234", "def5678"],
  "approved_by": "amit"
}
```

### 2. Research Findings (`chief-decifer/state/research/`)

Written by a scheduled Cowork task that runs real web searches on a regular interval.

```json
{
  "research_id": "res-2026-03-27-regime",
  "date": "2026-03-27",
  "topic": "HMM regime detection for trading",
  "queries_run": [
    "hidden markov model regime detection trading python 2025",
    "hmmlearn vs pomegranate regime classification"
  ],
  "findings": [
    {
      "title": "hmmlearn vs pomegranate comparison",
      "source_url": "https://...",
      "summary": "hmmlearn is simpler, pomegranate supports more distributions",
      "relevance": "high",
      "applicable_to": "roadmap/03-hmm-regime-detection.md"
    }
  ],
  "synthesis": "For Decifer's use case, hmmlearn with 3 states (bull/bear/neutral) is the pragmatic choice...",
  "search_engine": "cowork_websearch"
}
```

### 3. Feature Specs (`chief-decifer/state/specs/`)

Written by Cowork after Amit and Claude discuss and design a feature but haven't built it yet.

```json
{
  "id": "feat-regime-filter",
  "title": "Market Regime Filter",
  "status": "spec_complete|in_progress|complete|blocked",
  "priority": "P0|P1|P2",
  "designed_date": "2026-03-27",
  "started_date": null,
  "completed_date": null,
  "summary": "VIX + breadth-based regime classifier to gate entries",
  "approach": "Use hmmlearn with 3 states, train on SPY + VIX + TICK...",
  "files_affected": ["signals.py", "risk.py"],
  "dependencies": ["feat-vix-integration"],
  "roadmap_ref": "roadmap/03-hmm-regime-detection.md",
  "branch": "feat/regime-filter",
  "approved_by": "amit"
}
```

### Chief Dashboard Panels

| Panel | Data Source | Reads From |
|-------|------------|------------|
| **Pipeline** | Feature specs | `state/specs/` |
| **Test Results** | pytest output | runs `pytest`, displays results |
| **Recent Activity** | Session logs | `state/sessions/` |
| **Research** | Scheduled findings | `state/research/` |
| **Git History** | git log | `.git/` |
| **Code Health** | file stats, lint | source files (read-only) |

---

## Development Workflow

### Branching Strategy

- **`main`** (or `master`) — always a working version. Never commit directly.
- **Feature branches** — `feat/regime-filter`, `feat/short-scanner`, etc.
- **Bugfix branches** — `fix/stale-positions`, `fix/reconnect-race`, etc.
- Merge to main only after tests pass and Amit approves.
- If something breaks on main, revert to previous commit.

### Feature Lifecycle

```
1. Research (scheduled task or Cowork session)
   → findings written to state/research/
   → Chief shows on Research panel

2. Discuss (Cowork session with Amit)
   → talk through approach, trade-offs, architecture
   → spec written to state/specs/ with status: spec_complete
   → Chief shows on Pipeline panel as "Ready to Build"

3. Build (Cowork session)
   → create feature branch
   → write code + tests together
   → spec status updated to: in_progress
   → Chief shows "Building"

4. Review (Amit reviews in Cowork)
   → run tests, verify behavior
   → Amit approves or requests changes

5. Merge (Cowork session)
   → merge branch to main
   → spec status updated to: complete
   → session log written (Amit approves summary)
   → Chief shows "Complete" and logs activity

6. Monitor (Chief dashboard)
   → ongoing test results, signal performance
   → catches regressions
```

### Session Protocol

Every Cowork session that produces code follows this protocol:

1. **Start** — Review what's pending on the roadmap/specs
2. **Work** — Build feature or fix bug on a branch
3. **Test** — Run tests, verify behavior
4. **Log** — I draft a session summary, Amit approves before saving (Option B)
5. **Push** — Commit and push to GitHub

### Scheduled Research Task

A Cowork scheduled task runs on a configurable interval (daily recommended) to:

1. Search the web for topics relevant to the roadmap and current features
2. Write structured findings to `state/research/`
3. Chief picks them up and displays on the Research panel

Research uses real web search (WebSearch/WebFetch), NOT LLM simulation. Every finding includes a source URL that can be verified.

---

## Repository Structure

```
decifer-trading/
├── Core Modules (25 Python files)
│   ├── bot.py          — main orchestrator
│   ├── orders.py       — execution engine
│   ├── signals.py      — 9-dimension signal scoring
│   ├── risk.py         — position sizing, Kelly, drawdown
│   ├── dashboard.py    — trading UI (port 8080)
│   ├── agents.py       — 6-agent Claude pipeline
│   ├── scanner.py      — universe builder
│   └── ... (18 more)
│
├── docs/               — strategy, config guide, decisions
├── journals/           — daily dev notes
├── roadmap/            — 8 feature specs for bias fix
├── tests/              — pytest suite (written alongside features)
├── .env                — secrets (never committed)
├── .env.example        — template for new setups
├── requirements.txt    — Python dependencies
└── .github/workflows/  — CI (GitHub Actions)
```

Chief Decifer lives in a **separate repo** (not in the main decifer-trading repo).

---

## Known Constraints

- **Free data only**: yfinance (thread-unsafe, rate-limited), TradingView Screener, Yahoo RSS, Finviz
- **Paper account only**: IBKR paper (DUP...) for all trading, never live
- **yfinance issues**: thread-safety bugs, data contamination between tickers, crumb failures
- **TA-Lib dependency**: requires system-level C library (`brew install ta-lib`)

---

## GitHub

- **Repo**: https://github.com/DeciferBot/decifer-trading (private)
- **Branch protection**: main should always be deployable
- **Secrets**: all in `.env`, never in source code

---

## Roadmap (Active)

Fixing structural bullish bias — 8 features with dependency graph:

**Phase A (immediate):** Raise consensus threshold, short-candidate scanner, directional skew dashboard
**Phase B (core refactor):** Direction-agnostic signals, mean-reversion dimension
**Phase C (validation):** Signal validation with Alphalens
**Phase D (intelligence):** HMM regime detection, walk-forward weight calibration

See `roadmap/` directory for full specs.
