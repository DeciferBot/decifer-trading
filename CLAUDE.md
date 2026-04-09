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
3. COMMIT TO MASTER — push directly to master unless this is a Tier 3 multi-session rewrite
4. TEST — run relevant tests before declaring anything done
5. DRAFT SUMMARY — write session log entry for Amit to approve (do not commit without approval)
6. COMMIT & PUSH — only after Amit approves the summary

---

## Branching Rules

- Push directly to `master` by default — solo developer, no PRs, no feature branches
- Exception: Tier 3 architectural rewrites that span multiple sessions use a `feat/<feature-name>` branch to keep master stable while the work is incomplete
- Merge a Tier 3 branch only after: tests pass AND Amit explicitly approves

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

- Data sources (priority order):
  1. **Alpaca Algo Trader Plus** (PRIMARY — paid, active, $99/mo): real-time quotes, historical bars, streaming, options Greeks, snapshots, market calendar, corporate actions. Use this first for ALL market data needs. Exploit the full subscription — do not default to free alternatives when Alpaca can do the job.
  2. **Alpha Vantage** (paid, active): earnings calendar, fundamentals, macroeconomic data not covered by Alpaca.
  3. **IBKR TWS**: execution and order management only. Use `reqHistoricalData` only when Alpaca data is unavailable or insufficient.
  4. **yfinance**: daily bars and index data, fallback only — never preferred over Alpaca.
  5. TradingView Screener, Yahoo RSS, Finviz — supplementary screening and news.
- Streaming: ACTIVE via Alpaca Algo Trader Plus — real-time data and streaming unlocked. Use it.
- Paper account only: IBKR paper (DUP...). No live order submission
- TA-Lib requires system C library — always check availability before using
- No secrets, credentials, or .env file content in any commit
- Never run `git reset --hard`, `git push --force`, or `git clean -f` without explicit Amit instruction

---

## Architecture & Vision Integrity (paramount)

The Decifer project has a deliberate vision. All code must serve and sustain that vision — not diverge from it, not compromise it for short-term convenience.

**Build from first principles, not patches.**
- The simplest correct design is the first-principles answer. First principles does not mean thorough or complex — it means understanding the problem clearly enough to solve it simply.
- Every change must be designed correctly from the ground up. If the existing implementation is wrong, rewrite it properly — do not layer fixes on top of broken foundations.
- Patching is prohibited. A patch that makes broken code "work" is worse than no code, because it hides the structural problem and accumulates debt.
- If a component needs to change, understand why it exists first, then replace it with something architecturally sound.

**Simplicity is correctness.**
- The simpler the code, the fewer the errors. Complexity is where bugs live.
- If two implementations solve the same problem, always choose the simpler one — without exception.
- A function that does one thing clearly is better than a function that does three things cleverly.
- If a solution feels complicated, it is a signal the problem is not fully understood yet. Stop and rethink before writing.

**Sustainability over speed.**
- Code must be maintainable long-term. Clarity, modularity, and correct separation of concerns are non-negotiable.
- Do not introduce complexity that cannot be explained. If a design requires a long comment to justify it, the design is probably wrong.
- Prefer fewer, well-designed abstractions over many small ones. Every layer of indirection must earn its place.

**Acceptance of the vision.**
- Before writing any code, Claude must understand how the change fits the Decifer system architecture and trading logic.
- If a request conflicts with the project's architecture or vision, Claude must flag the conflict to Amit before proceeding — not silently work around it.
- No component should be built in isolation. Every module must fit cleanly into the existing data flow, signal pipeline, and execution model.

**No monoliths.**
- Every module has one clearly defined responsibility. If you cannot state what a module does in one sentence, it is doing too much.
- A function longer than 30 lines is a signal it is doing more than one thing. Stop and split it.
- A module longer than 200 lines is a signal it has grown beyond its scope. Before adding more, ask whether the new logic belongs elsewhere.
- Never add a parameter to an existing function to handle a new case. If a new case arises, create a new function or a new module.
- Never add logic to a module because it is "close enough" to what that module already does. Proximity is not ownership.
- State belongs in dedicated state modules. A module that accumulates state it was not originally designed to hold must be refactored, not extended.
- When a module starts importing from many other modules, it is a sign it has taken on too much. Dependencies should flow in one direction.

**Before adding to any existing module, ask:**
1. Is this genuinely this module's responsibility, or am I adding here out of convenience?
2. Will this make the module harder to understand or test?
3. Should this be a new module instead?

If the answer to 1 is no, or 2 is yes — create a new module.

**Structural questions to ask before any implementation:**
1. Does this belong in the existing architecture, or does it require a design decision first?
2. Am I patching something broken, or building something correct?
3. Does this change sustain or erode the long-term vision of the system?

---

## Code Fix Integrity (mandatory)

- **No hallucination.** Never invent function names, method signatures, module paths, variable names, or API behaviours that have not been confirmed by reading the actual source. If unsure, read the file first.
- **Suggestive fixes must be validated.** Any code change proposed without first reading the target file is speculative. Before writing or editing code, always read the relevant file(s) to confirm current implementation. Do not suggest a fix based on assumption alone.
- If a fix cannot be confirmed by reading existing code, state the uncertainty explicitly and ask Amit before proceeding.

---

## Pre-Existing Errors — Fix Without Fail (mandatory)

When implementing any change, Claude must identify and fix all pre-existing errors in the affected code, not just the ones directly relevant to the task.

- **Do not ignore errors encountered while reading or editing.** If a file contains broken logic, incorrect imports, stale references, or known bugs unrelated to the current task — fix them in the same session. Do not silently work around them.
- **Never suppress or hide a pre-existing error to make new code compile or pass tests.** A green test that papers over a real error is worse than a failing test.
- **Pre-existing errors must be fixed before the session is closed.** Flagging verbally or noting in a response is not compliance. The error must be resolved in code.
- **Pre-existing errors must be listed in the session summary** so Amit has a full record of what was fixed.
- Only if a pre-existing error requires a Tier 3 architectural decision that cannot be resolved in the current session may it be deferred — and only with explicit Amit approval to defer. In that case it must be logged to `chief-decifer/state/backlog.json` immediately, not at end of session.

---

## Change Validation & Backtesting (mandatory)

No change is complete until it has been correlated against the existing codebase and verified not to break any functioning behaviour.

**Before writing any new code:**
1. Read all modules the change will touch or that depend on the changed module.
2. Identify every call site, data consumer, and downstream effect of the change.
3. If the change touches signal generation, scoring, filtering, position sizing, or order submission — trace the full path from signal origin to order execution and confirm nothing breaks.

**After writing new code:**
1. Run all tests in `tests/` that cover the affected modules. Do not declare done until they pass.
2. For signal pipeline changes: verify that existing signal dimensions still fire correctly and that IC tracking is not disrupted.
3. For order/execution changes: verify that bracket logic, position sizing, and risk gates still function as designed.
4. If a new feature cannot be traced end-to-end through the existing bot flow (`bot_trading.py` → `signal_pipeline.py` → `position_sizing.py` → `bot_ibkr.py`), it is not ready to commit.

**Do not commit a change that:**
- Has not been run against existing tests
- Has not been traced through its downstream effects in the live bot flow
- Adds a feature that could silently override or conflict with existing logic without Amit's explicit sign-off
- Has only been tested in isolation without confirming integration with dependent modules

**When in doubt, do not commit.** Present the analysis to Amit and ask for explicit approval before proceeding.

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
