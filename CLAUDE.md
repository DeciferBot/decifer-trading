# Decifer Trading — Claude Governance Rules
# Version 1.0 | Based on Ruflo patterns adapted for Decifer

## Identity & Role

Claude (Cowork) is the sole coding and research agent for Decifer Trading.
Amit is the decision maker, domain expert, and final approver for all merges, summaries, and specs.
Chief Decifer is read-only. It reads state files written by Cowork. Claude never modifies Chief Decifer's display logic or dashboard agents.

---

## Golden Rule: Batch Everything

All operations in a session MUST be batched into as few round-trips as possible.
- Read all needed files FIRST, in one parallel set of tool calls
- Group all edits together before reporting back
- Combine all bash commands into single calls using `&&`
- Never read one file, report, then read another — pre-plan and batch

Why this matters: every back-and-forth re-reads the full context window. Ten single operations cost 10x more than one batch of ten.

---

## Session Protocol (mandatory)

Every session must follow this sequence:

1. LOAD CONTEXT — read checkpoint, last 2 session logs, active specs, latest research
2. REVIEW PENDING — confirm what branch is active, what feature is in flight
3. BUILD ON BRANCH — all code changes on a `feat/...` or `fix/...` branch, never main
4. TEST — run relevant tests before declaring anything done
5. DRAFT SUMMARY — write session log entry for Amit to approve (do not commit without approval)
6. COMMIT & PUSH — only after Amit approves the summary

---

## Branching Rules

- Never commit directly to `main`
- Feature branches: `feat/<feature-name>`
- Bug fix branches: `fix/<bug-description>`
- Merge only after: tests pass AND Amit explicitly approves

---

## Data Contracts (sacred — do not change paths)

Cowork writes to these three folders. Chief Decifer reads from them. Path structure must never change.

| Data Type       | Path                                    | Written by   | Read by      |
|-----------------|------------------------------------------|--------------|--------------|
| Session logs    | `chief-decifer/state/sessions/`         | Cowork       | Chief Decifer |
| Research        | `chief-decifer/state/research/`         | Cowork       | Chief Decifer |
| Feature specs   | `chief-decifer/state/specs/`            | Cowork       | Chief Decifer |
| Backlog         | `chief-decifer/state/backlog.json`      | Cowork       | Chief Decifer |

Session log filename format: `YYYY-MM-DD_<topic>.json`
Research filename format: `YYYY-MM-DD_<topic>.json`
Spec filename format: `feat-<feature-id>.json`

---

## Task Complexity Routing

Match task complexity to appropriate effort level. Do not over-engineer simple tasks.

**Tier 1 — Fast (read/check/scan):**
- Reading files, checking git status, scanning for patterns
- Running existing tests, checking data output
- Quick data pulls from yfinance, Finviz, screener

**Tier 2 — Standard (implement/fix):**
- Bug fixes, single-module changes, adding a function
- Writing or updating a spec or session log
- Research synthesis from web data

**Tier 3 — Deep (architect/redesign):**
- Multi-file refactors touching 3+ modules
- Bias correction analysis across signal dimensions
- New phase planning, roadmap decisions
- Security or data integrity reviews

Always identify the tier before starting. Tier 3 tasks require explicit Amit approval of the approach before any code is written.

---

## File Organisation

Source code: Python modules in repo root or `src/`
Tests: `tests/`
Roadmap: `roadmap/`
State files: `chief-decifer/state/` (written per data contracts above)
Claude config: `.claude/` (hooks, helpers, memory — do not expose to Chief Decifer)

Do not create files in the root unless they are core Python modules. No stray test scripts, working files, or debug outputs in root.

---

## Constraints (hard limits)

- Free data only: yfinance (thread-safety workaround required), TradingView Screener, Yahoo RSS, Finviz
- Paper account only: IBKR paper (DUP...). No live order submission
- TA-Lib requires system C library — always check availability before using
- No secrets, credentials, or .env file content in any commit
- Never run `git reset --hard`, `git push --force`, or `git clean -f` without explicit Amit instruction

---

## Memory System

Claude maintains persistent context in `.claude/memory/`. At session start, the session-start hook automatically loads:
- Last 2 approved session summaries
- All specs with status `in_progress` or `pending`
- Latest research findings
- Active checkpoint (branch, open todos, last touched files)

This context is injected before the first prompt. Claude should acknowledge the loaded context at session start rather than asking "where were we?"

---

## Security Gates

Before any destructive operation, Claude must state what will be destroyed and get explicit confirmation:
- Deleting files
- Force pushing
- Dropping database tables or resetting state
- Modifying Chief Decifer's read paths or data contract folders

---

## Chief Decifer — Eyes Only

Chief Decifer rules (non-negotiable):
- It reads state. It does not write code.
- It has no Coder, Tester, or Researcher agents. Only Dashboard agent.
- Claude does not modify Chief Decifer's agent loop, dashboard rendering, or port config (8181)
- All data Chief Decifer displays comes from the three state folders above

---

## Commit Message Format

```
<type>(<scope>): <short description>

<body — what changed and why, 2-3 sentences>

Approved-by: Amit
```

Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`
