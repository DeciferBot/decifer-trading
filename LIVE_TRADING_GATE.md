# Live Trading Gate — Decifer Trading
## Version 1.1 | 2026-03-30

This document is the single source of truth for all criteria that must be met
before live-money trading is permitted. Three sequential gates must be cleared in
order. No gate may be bypassed. Amit must explicitly sign off on each transition.

---

## Current Status

| Gate | Status | Progress |
|------|--------|----------|
| Gate 0 — Alpha Validation | **BLOCKED** | 0/50 closed trades with positive expectancy |
| Gate 1 — Phase 1 Exit | Locked (requires Gate 0) | 0/200 closed trades |
| Gate 2 — Live Trading Prerequisites | Locked (requires Gate 1) | Telegram not configured |
| Gate 3 — Founder Sign-Off | Locked (requires Gate 2) | Awaiting all prior gates |

_Status is informational only. Authoritative check: `phase_gate.check_alpha_gate()` and `phase_gate.get_status()`._

---

## Gate 0 — Alpha Validation Gate

**The signal model has no demonstrated alpha until this gate is cleared.**
All downstream work (new signal dimensions, infrastructure, live trading) is
built on an unvalidated foundation until these criteria are met.

### Hard Rules (enforced by `phase_gate.assert_alpha_gate_passed()`)

- [ ] **50 closed paper trades** logged to `data/trades.json`
- [ ] **Positive average PnL per trade** across those 50 trades (expectancy > $0)

### What this gate blocks

While Gate 0 is open, the following are **prohibited**:

| Blocked Work | Reason |
|---|---|
| New signal dimensions (mean-reversion, HMM, walk-forward weights) | Would add complexity to an unproven foundation |
| Infrastructure work (Docker, cloud, multi-user) | Would harden an untested system |
| Advancing the live trading gate | Cannot risk real capital on unvalidated alpha |
| Phase B/C/D backlog items | All depend on Gate 0 being cleared |

### How to clear Gate 0

1. Accumulate 50+ closed paper trades in `data/trades.json`
2. Verify positive expectancy: `python3 -c "from phase_gate import check_alpha_gate; import json; print(json.dumps(check_alpha_gate().as_dict(), indent=2))"`
3. Review trade quality with Amit — win rate, regime distribution, worst drawdown
4. Amit signs off before any Phase B work begins

---

## Gate 1 — Phase 1 Exit Criteria

_Requires Gate 0 to be cleared first._

All of the following must be true simultaneously:

- [ ] **200+ closed paper trades** logged to `data/trades.json`
- [ ] **Test suite ≥ 80% pass rate** (run `pytest`)
- [ ] **30+ consecutive paper trading days** without a critical bug or system halt
- [ ] **Amit explicitly sets** `config["phase_gate"]["current_phase"] = 2`

### Checked by

`phase_gate.get_status()` — `criteria_met` dict and `phase1_complete` bool.

---

## Gate 2 — Live Trading Prerequisites

_Requires Gate 1 to be cleared first (current_phase ≥ 4)._

All of the following must be true:

- [ ] **Telegram kill switch configured** — `config["telegram"]["bot_token"]` and
  `config["telegram"]["authorized_chat_ids"]` must be set before any live account
  is activated. Emergency stop must work even when the web dashboard is unreachable.
- [ ] **Live account IDs set** — `config["accounts"]["live_1"]` populated
- [ ] **Candle gate enabled** — `config["candle_required"] = True`
- [ ] **MTF gate set to "hard"** — `config["mtf_gate_mode"] = "hard"`
- [ ] **Agents consensus raised to 4** — `config["agents_required_to_agree"] = 4`
- [ ] **Risk parameters reverted to live values** — see comments in `config.py`
- [ ] **Amit explicitly sets** `config["phase_gate"]["current_phase"] = 4`

### Checked by

`phase_gate.validate()` — returns a list of violation strings (empty = clear).

---

## Gate 3 — Founder Sign-Off

_Requires Gate 2 to be cleared first. This gate is human-enforced — no code can substitute for it._

Gates 0–2 are verified programmatically. Gate 3 is Amit's explicit, deliberate decision
to deploy real capital. It cannot be automated or inferred.

### Required actions (all must be completed in order)

- [ ] **Read the full paper trading session log** covering ≥ 30 consecutive trading days
- [ ] **Inspect the top 5 trades by absolute PnL** — confirm Gate 0 expectancy is not driven
      by 1–2 outlier trades. If removing the top 2 trades flips expectancy negative, do not proceed.
- [ ] **Confirm per-dimension IC** — at least 3 of 9 signal dimensions show positive
      information coefficient over the paper trading period
- [ ] **Review the weekly review logs** in `chief-decifer/state/sessions/` — no unresolved
      action items, no systemic issues flagged
- [ ] **Amit sets** `config["phase_gate"]["founder_approved_live"] = true` in `config.py`
- [ ] **Amit commits** with message:
      `chore(live-gate): founder approval — live trading authorised`

### What "founder approved" means

This is a deliberate acknowledgement that:
1. You have read the evidence, not just the metrics
2. You accept the risk of real capital loss
3. The system's behaviour matches your expectations from watching it in paper mode

### Enforcement

`phase_gate.validate()` will be updated to check `founder_approved_live` before clearing Gate 2.
Until that code change is made, this gate is human-process only.

---

## Governance

- This document is owned by Amit (decision maker and final approver).
- Cowork (Claude) enforces gates programmatically but **cannot advance any gate**
  without Amit's explicit instruction.
- Gate criteria may only be changed by editing this file **and** updating
  `config.py` `phase_gate` section in the same commit with Amit's approval.
- The word "clear" means all checkboxes above are ticked AND Amit has reviewed.

---

## Quick Reference — Enforcement Functions

```python
from phase_gate import (
    check_alpha_gate,        # Returns AlphaGateStatus (non-raising)
    assert_alpha_gate_passed, # Raises PhaseGateViolation if Gate 0 blocked
    get_status,              # Full PhaseStatus including alpha_gate
    validate,                # Returns list of Phase 4 gate violations
    validate_or_raise,       # Raises on first violation
    assert_feature_allowed,  # Check a specific frozen feature
)

# Check Gate 0
status = check_alpha_gate()
print(status.gate_passed, status.closed_trades, status.expectancy)

# Enforce Gate 0 before starting new signal dimension work
assert_alpha_gate_passed()  # raises PhaseGateViolation if not cleared

# Full status for dashboard
full = get_status()
print(full.alpha_gate.as_dict())
```
