# Paper Trading Observation Protocol — Decifer Trading
## Version 1.0 | 2026-03-30

This document defines how paper trading sessions are run, what is reviewed,
and the exact exit criteria for advancing to Gate 1 and live capital.
It is the companion document to `LIVE_TRADING_GATE.md`.

---

## Operating Mode

- Bot runs continuously on paper account `DUP481326` during US market hours
- Full scan cycle runs every `CONFIG['scan_interval_mins']` minutes (default 30)
- Paper risk settings are tuned for **data generation, not capital preservation**:
  - `risk_pct_per_trade`: 3% (vs live: 4%)
  - `max_positions`: 20 (vs live: 12)
  - `daily_loss_limit`: 10% (vs live: 6%)
- No manual intervention during normal sessions unless a critical bug is observed

---

## Weekly Review (every Monday before market open)

### 1. Gate progress check

```bash
python3 -c "from phase_gate import check_alpha_gate, get_status; import json; s = get_status(); print(json.dumps({'closed_trades': s.closed_trades, 'alpha_gate': s.alpha_gate.as_dict() if s.alpha_gate else None, 'phase1_complete': s.phase1_complete}, indent=2))"
```

### 2. Trade quality review (`data/trades.json`)

- Win rate (target: understand distribution, not hit a number)
- Expectancy per trade (Gate 0 criterion: must be > $0)
- Average hold time — are trades exiting at intended horizons?
- Worst single-trade loss — is it within daily loss limit?
- Exit reason breakdown: `agent_sell` vs `stop_loss` vs `timeout`
  - Note: `agent_sell` conflates system exits AND manual dashboard closes — do not
    attribute its win rate to system logic without verifying individually

### 3. Signal quality review (`data/signals_log.jsonl`)

- Per-dimension score distribution — are any dimensions always 0 or always max?
- Direction vote breakdown — LONG vs SHORT ratio (skew check)
- Correlation between aggregate score and forward PnL (even rough eyeball)

### 4. Execution quality review (`data/orders.json`)

- SUBMITTED vs FILLED ratio — are orders executing or timing out?
- REJECTED count and reason codes — any systematic rejection pattern?
- Slippage: compare `limit_price` vs `fill_price`

### 5. Infrastructure health

- Check `equity_history.json` for equity curve continuity (no gaps > 1 trading day)
- Confirm `data/signals_log.jsonl` is growing (not stale)
- Run `pytest` — pass rate must stay ≥ 80% throughout paper phase

### 6. Session log

Write a session log entry to `chief-decifer/state/sessions/YYYY-MM-DD_weekly-review.json`:

```json
{
  "date": "YYYY-MM-DD",
  "type": "weekly_review",
  "closed_trades": 0,
  "expectancy": null,
  "win_rate": null,
  "worst_loss": null,
  "fill_rate": null,
  "pytest_pass_rate": null,
  "observations": "",
  "action_items": [],
  "gate_0_status": "blocked | cleared",
  "approved_by": "Amit"
}
```

---

## Monthly Review (last Friday of each month)

In addition to the weekly review items:

### Alpha Decay tab

- Open dashboard Alpha Decay tab
- Review T+1 / T+3 / T+5 / T+10 forward return distributions
- Check whether high-score trades (≥38) outperform low-score trades — this is the primary signal quality indicator
- If T+1 distribution is flat (no edge at any horizon), flag for Amit review before continuing

### Dimension flag review

- For each dimension, check 4-week rolling IC (signal vs forward return)
- If a dimension shows IC < 0 for 4+ consecutive weeks, propose disabling via `dimension_flags` in `config.py`
- Proposal must be approved by Amit before config change is committed

### Gate progress summary

- Closed trades count vs 50 (Gate 0) and 200 (Gate 1)
- Estimated weeks to Gate 0 at current trade rate
- Any systemic issues that would invalidate trades already logged

---

## Exit Criteria — Gate 0 (advance to Phase B work)

All three must be true:

1. `check_alpha_gate().gate_passed == True`
   — 50 closed paper trades AND positive expectancy in `data/trades.json`
2. Amit reviews the trade log manually and confirms expectancy is not driven by
   1–2 outlier trades (inspect top 5 trades by PnL absolute value)
3. No critical bug or unplanned system halt in the 7 trading days preceding sign-off

**Amit's sign-off action:** explicit verbal approval before any Phase B work begins.
Cowork logs the sign-off in the session log.

---

## Exit Criteria — Gate 1 (advance toward live trading consideration)

All four must be true (see `LIVE_TRADING_GATE.md` Gate 1 for full detail):

1. 200+ closed paper trades
2. `pytest` pass rate ≥ 80%
3. 30+ consecutive paper trading days without a critical bug
4. Amit explicitly sets `config["phase_gate"]["current_phase"] = 2`

---

## What Disqualifies a Trade from the Gate Count

A trade does NOT count toward the 50/200 thresholds if:

- It was manually backfilled (not entered by the live signal pipeline)
- It was manually closed from the dashboard without a system exit signal
- The entry signal score is missing from `data/signals_log.jsonl`

The current count of **agent-scored trades** is the only number that matters for gate advancement.
Backfilled trades from IBKR history are useful for analysis but cannot substitute for live agent
decisions.

---

## When to Stop Paper Trading Early

Stop the paper session and alert Amit if:

- Daily loss limit is hit (`daily_loss_limit = 10%`) — bot auto-halts, but confirm manually
- IBKR reconnection exhausts all 10 attempts — manual restart required
- `data/orders.json` shows > 20% ORDER_REJECTED in any single session
- Any order is submitted to a live account (gate violation)

---

## Governance

- This protocol is owned by Amit and reviewed at Gate 0 clearance
- Cowork runs reviews and writes session logs; Amit approves before any gate advance
- Protocol changes require a commit with Amit's explicit approval
