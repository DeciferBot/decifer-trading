# Decifer Trading — Decision Log

> Every significant design decision, parameter change, or architectural choice gets logged here with the reasoning. This is the "why" behind the "what."
>
> Format: Date → Decision → Context / Reasoning

---

## 2026-03-25 — Established Documentation System

**Decision**: Use git + Markdown docs as the primary version control and documentation system.

**Context**: The codebase is evolving daily through brainstorming and programming sessions. Word docs in `docs/` serve as polished references but can't be diffed in git. Markdown companions track the living, changing logic while Word docs get regenerated periodically.

**Alternatives considered**: Notion (too disconnected from code), Wiki (overkill for solo/small team), inline comments only (can't see the big picture).

---

## Pre-2026-03-25 — Historical Decisions (Reconstructed)

These decisions are inferred from the current codebase. Future entries will be logged as they happen.

### 6-Agent Architecture
**Decision**: Use 6 specialised Claude agents rather than a single monolithic prompt.

**Reasoning**: Each agent has a focused role and can be tuned independently. The Devil's Advocate agent specifically exists to counterbalance confirmation bias. The Risk Manager has veto power to prevent the other agents from overriding safety limits.

### Agent Agreement Threshold = 3 of 6
**Decision**: Require 3+ agents to agree (was previously 4).

**Reasoning**: Currently set at 3 in config. Lower threshold = more trades taken. This is a key parameter that can be adjusted based on market conditions and performance data.

### Signal Engine: 6 Independent Dimensions
**Decision**: One indicator per dimension, no overlapping oscillators.

**Reasoning**: Avoid the common trap of using RSI + Stochastic + CCI which all measure the same thing (momentum). Each of the 6 dimensions (Trend, Momentum, Squeeze, Flow, Breakout, Confluence) measures something fundamentally different.

### Options: ATM Delta Targeting (0.50)
**Decision**: Target delta 0.50 instead of the more common 0.30–0.40 for directional trades.

**Reasoning**: ATM options provide maximum leverage per dollar of premium. The slightly higher premium cost is offset by better probability and more responsive Greeks.

### Inverse ETFs Instead of Short Selling
**Decision**: Use inverse ETFs (SPXS, SQQQ, UVXY) for bearish exposure rather than direct shorting.

**Reasoning**: Simpler execution, no borrow costs, no margin complications. Trade-off is tracking error on leveraged products, but acceptable for short-duration trades.
