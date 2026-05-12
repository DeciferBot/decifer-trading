# Rotation Paper Validation — Design Document

**Created:** 2026-05-13  
**Status:** Active — first validation run complete  
**Script:** `scripts/rotation_paper_validation.py`  
**Tests:** `tests/test_rotation_paper_validation.py`

---

## 1. Purpose

The rotation paper validation harness simulates what a rotation policy would have done,
then compares the hypothetical rotated book against actual outcomes.

This is a **read-only offline diagnostic tool**.  It does not connect to any broker,
generate any orders, or modify any runtime state.  It produces validation artifacts in
`data/rotation_paper_validation/` only.

---

## 2. Why Paper Validation Is Justified Now

Three consecutive sessions have returned `ROTATION_SHADOW_CONFIRMED`:

| Session | Verdict | Strongest blocked | Gap vs book |
|---------|---------|-------------------|-------------|
| 2026-05-11 | ROTATION_WATCH | AVGO 80 | >20 |
| 2026-05-12 (prior) | ROTATION_SHADOW_CONFIRMED | AVGO 80 | +25.2 |
| 2026-05-12 (latest) | ROTATION_SHADOW_CONFIRMED | DVA 75 | +20.2 |

The same three positions (XLK 26, XLE 23, WDC 27) appear as the top shadow rotation
candidates across all three sessions.  The pattern is stable enough to merit quantitative
scenario evaluation.

Rotation observability JSONL is populated correctly (commit 1360e79 confirmed working).
The shadow report now reads from structured JSONL rather than log-regex fallback.

---

## 3. Why Live Rotation Remains Blocked

Live rotation requires all of the following, none of which are met:

| Requirement | Status |
|-------------|--------|
| Rotation policy specification approved by Amit | Not approved |
| Gate G10 passed (exact required notional from sizing engine) | **Provisional** — estimated only |
| Shadow-only policy module designed and reviewed | Not started |
| Paper validation shows positive uplift across ≥2 sessions | Pending outcomes |
| Multi-session paper validation confirmed | Pending |

**G10 specifically:** `estimated_notional` = `portfolio_value × max_single_pct` is a
conservative upper-bound ceiling.  It is not the sizing engine's intended allocation for
the specific candidate.  G10 passes only when exact required notional is logged by the
sizing engine at block time.  Until G10 passes, all capacity figures are provisional and
labelled `[ESTIMATE — G10 provisional]`.

---

## 4. Inputs

| File | Purpose | Required |
|------|---------|----------|
| `data/rotation_observability/margin_blocks.jsonl` | Block events with scores and notional | Primary |
| `data/rotation_observability/position_snapshots.jsonl` | Book state at each block | Primary |
| `data/training_records.jsonl` | Closed trade outcomes for forward P&L matching | Forward outcomes |
| `data/rotation_shadow_reports/*.json` | Multi-session context (optional) | Optional |

The script does not read `data/trades.json`, `data/trade_events.jsonl`,
`data/positions.json`, or any log files as primary sources.

---

## 5. Outputs

All outputs are written to `data/rotation_paper_validation/`.

| File | Content |
|------|---------|
| `report_<UTC>.json` | Machine-readable full report with all scenario fields |
| `report_<UTC>.txt` | Human-readable formatted report |

**Output directory is safe to delete.**  All outputs are re-generatable by re-running
the script.  No runtime state is modified.

---

## 6. Scenario Methodology

For each qualifying margin block, the script builds three scenarios:

| Scenario | Action |
|----------|--------|
| A | Exit the single weakest position (top-1 shadow candidate) to free capacity |
| B | Exit the top-2 shadow candidates |
| C | Exit the top-3 shadow candidates |

Each scenario computes:
- `theoretical_capacity_released`: sum of (qty × entry_price) for the exit set
- `capacity_sufficient_estimated`: whether released capacity ≥ estimated_notional
- Forward outcomes for the blocked candidate and each shadow exit (where available)
- `relative_uplift`: blocked candidate P&L minus shadow exit candidates P&L

**What constitutes a qualifying block:**

A margin block qualifies for paper validation only when all conditions are met:

1. `exp_code == "margin_gross_cap_block"` — rotation can address this type
2. `candidate_score >= --min-blocked-score` (default 70)
3. `gap_vs_book >= --min-gap-vs-book` (default 20)
4. A position snapshot exists at or before the block timestamp
5. At least 3 open positions have score < 50
6. At least 1 open position has score < 35

**Excluded block types** (rotation cannot help these):
- `account_values_stale_block` — IBKR data stale; rotation does not resolve
- `spread_block` — wide spread; rotation does not resolve
- `stale_price_block` — stale price data; rotation does not resolve

---

## 7. Capacity Methodology

Theoretical capacity released is calculated as:

```
theoretical_capacity_released = sum(qty × entry_price  for each shadow exit candidate)
```

This uses **entry notional** from the position snapshot, not mark-to-market value.
Entry notional slightly understates capacity when prices have risen and overstates it
when prices have fallen.  This is conservative and acceptable for paper validation.

`estimated_notional` is `portfolio_value × max_single_pct` (an upper-bound ceiling,
not the sizing engine output).  Gate G10 is provisional until exact required notional
is logged.

**All capacity figures are labelled `[ESTIMATE — G10 provisional]` in output.**

---

## 8. Outcome Methodology

Forward outcomes are matched from `data/training_records.jsonl`:

**For the blocked candidate:**  
Search for a training record where `symbol == blocked_symbol` and
`ts_fill` falls within `[block_ts, block_ts + lookahead_hours]`.
This represents a new entry that opened after the block (next scan cycle or later session).

**For shadow exit candidates:**  
Search for a training record where `symbol == candidate_symbol` and
`ts_close` falls within `[block_ts, block_ts + lookahead_hours]`.
This represents the position being closed naturally or by the bot during the window.

**Important limitation:**  
Shadow exit P&L reflects the **total realized P&L from original entry to close**,
not only the portion earned after the block timestamp.  If a shadow exit entered 5 days
before the block, its pre-block P&L is included.  This overstates the opportunity cost
of exiting for recent entries and understates it for older entries.  This is a known
limitation and is documented in `data_quality_gaps` of every report.

**Outcome status values:**

| Status | Meaning |
|--------|---------|
| `OUTCOME_AVAILABLE` | Both blocked candidate and all shadow exits have training records within the window |
| `OUTCOME_PENDING` | At least one outcome not yet recorded (positions still open, window not elapsed) |
| `INSUFFICIENT_DATA` | Core inputs missing — cannot evaluate |

---

## 9. Known Limitations

1. **G10 provisional:** `estimated_notional` is an upper-bound estimate.  Exact sizing
   engine output is not captured at block time.  All capacity figures are directional only.

2. **Shadow exit P&L attribution:** Total trade P&L, not post-block-only.  Overstates
   opportunity cost for new entries; understates for old carry positions.

3. **Book reconstruction uses entry notional:** Position snapshot records qty and entry
   price.  Mark-to-market value is not stored.  Capacity released is approximated.

4. **Blocked candidate outcome depends on future entry:** The blocked candidate may never
   enter (systemic block persists), enter much later (different market conditions), or
   enter the next cycle (similar conditions).  All three are aggregated by symbol+window.

5. **Single-session coverage:** Each report covers one date range.  Multi-session
   uplift confirmation requires running the script across multiple sessions after
   lookahead windows have elapsed.

6. **Training records contain closed trades only:** Positions still open at evaluation
   time will show `OUTCOME_PENDING` regardless of lookahead window length.

---

## 10. How to Run

**Basic run (current session):**
```bash
python3 scripts/rotation_paper_validation.py --since 2026-05-12
```

**With extended lookahead (48h for swing trades):**
```bash
python3 scripts/rotation_paper_validation.py --since 2026-05-12 --lookahead-hours 48
```

**Restrict to high-conviction blocks only:**
```bash
python3 scripts/rotation_paper_validation.py --since 2026-05-12 \
  --min-blocked-score 75 --min-gap-vs-book 25
```

**Custom output directory:**
```bash
python3 scripts/rotation_paper_validation.py --since 2026-05-12 \
  --output-dir /tmp/rotation_test/
```

**Run tests:**
```bash
python3 -m pytest tests/test_rotation_paper_validation.py -v
```

---

## 11. How to Interpret Verdicts

| Verdict | Meaning | What to do |
|---------|---------|-----------|
| `PAPER_VALIDATION_NO_OPPORTUNITIES` | No qualifying margin blocks in the date range | Check date range; consider loosening thresholds |
| `PAPER_VALIDATION_PENDING_OUTCOMES` | Opportunities exist but positions still open | Re-run after lookahead window elapses |
| `PAPER_VALIDATION_WEAK_SIGNAL` | Scenarios exist but hypothetical uplift is mixed or negative | Keep observing; do not proceed to policy design |
| `PAPER_VALIDATION_SUPPORTS_ROTATION` | Multiple scenarios show positive uplift across sessions | Proceed to `DESIGN_PAPER_ONLY_POLICY_SIMULATION` (with Amit approval) |
| `PAPER_VALIDATION_INSUFFICIENT_DATA` | Core inputs too incomplete to evaluate | Fix data gaps listed in `data_quality_gaps` |

**Important:** `PAPER_VALIDATION_SUPPORTS_ROTATION` does not authorise live rotation.
It authorises designing a shadow-only policy simulation module for Amit review.

---

## 12. Promotion Criteria: Paper Validation → Paper-Only Policy Simulation

All of the following must be true before designing a policy simulation module:

| Criterion | Required level |
|-----------|----------------|
| Sessions with `PAPER_VALIDATION_SUPPORTS_ROTATION` | ≥ 2 independent sessions |
| Positive relative uplift in scenario A (top-1 exit) | True in ≥ 2 sessions |
| Top shadow candidates stable across sessions | ≥ 2 of top-3 consistent |
| G10 resolution path identified | Documented, not necessarily implemented |
| Amit has reviewed and approved promotion | **Required** |

---

## 13. What Still Blocks Live Execution

Even after paper validation is complete and a policy simulation module exists,
the following are required before any live rotation:

1. **Rotation policy specification approved by Amit** — a formal written spec
   defining trigger conditions, ranking formula, capacity test, hold-protected
   position handling, and output contract.

2. **Gate G10 passed** — exact required notional from the sizing engine must be
   captured at block time.  Until then, capacity analysis is directional only.

3. **Paper-only policy simulation validated** — the simulation module must run
   for ≥ 3 sessions without errors and with interpretable outputs.

4. **Hold-protected position flag implemented** — positions with
   `hold_protected=True` must be excluded from rotation candidates.

5. **Explicit Amit approval for each promotion step** — paper validation → policy
   spec → policy simulation → shadow-only auto-recommendation → live rotation.
   Each step requires a separate approval.

**No step in this script or any diagnostic script authorises live rotation.**
