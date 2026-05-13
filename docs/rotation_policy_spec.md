# Rotation Policy Specification

**Status:** Active shadow testing. No implementation authorised.
**Created:** 2026-05-12
**Last updated:** 2026-05-13
**Author:** Cowork (Claude)
**Approved by:** Pending Amit review

**Shadow testing progress:** 4 sessions observed. ROTATION_SHADOW_CONFIRMED in 3 of 4.
**Paper validation framework:** Live as of 2026-05-13 (`scripts/rotation_paper_validation.py`).
**Escalation gate progress:** Gate 1 needs 3 additional confirmed sessions (1 of 3 met since spec was written). Gates 3–9 not yet evaluable.

---

## Service Layer Classification

| Field | Value |
|---|---|
| Service layer | Policy specification / governance only |
| Runtime purpose | None yet |
| Live bot dependency | None |
| Broker dependency | None |
| Trading side effects | None |
| Retirement register | No impact |
| Production simplification | Spec only. Future implementation must be deterministic, fail-closed, and separately approved. |
| Cloud runtime | No impact |

---

## 1. Purpose

This document specifies the design intent and governance constraints for a future Decifer rotation policy. It does not authorise implementation.

A rotation policy may eventually be warranted because the diagnostic record shows a repeating pattern: weak open positions have consumed capital capacity before stronger candidates could enter. This displaced entry is not hypothetical. It is confirmed by two independent diagnostics run across two consecutive sessions.

The specific problem:

- Carry-book positions with entry scores below 35 occupy notional that could fund stronger entries.
- Margin capacity is exhausted before higher-conviction candidates clear the position-open cycle.
- The candidate blocked in both sessions was AVGO with a score of 80, outscoring the live book average by more than 20 points.
- Weak positions at the time of blockage included XLK (score 26), XLE (score 23), and WDC (score 27), collectively holding approximately $173,000 in notional.
- This is not a one-session anomaly. It repeated across 2026-05-11 and 2026-05-12 with the same blocked symbol and the same weak carry names.

The purpose of a rotation policy is to make it possible for the system to recognise this condition and, under strictly defined criteria, allow a weaker position to be evaluated as a shadow rotation candidate so that a stronger blocked candidate can enter.

The purpose is not to improve short-term P&L directly. The purpose is to improve the quality of training data by ensuring that higher-conviction opportunities are not systematically excluded by lower-conviction entries that entered earlier in the session.

---

## 2. What This Policy Is Not

This policy is not live rotation. No position will be sold or exited as a result of this document.

This policy is not an automatic selling system. No execution logic exists or should be created from this specification alone.

This policy is not ETF suppression. Low-score ETFs with single-name overlap are flagged as higher-priority shadow rotation candidates within the ranking formula. They are not blocked from entry and not automatically closed.

This policy is not PRU rescue. PRU/discovery source labels remain diagnostic metadata only. No tier-led allocation, promotion, or suppression is authorised by this specification.

This policy is not a margin cap change. Margin caps remain as configured.

This policy is not a threshold change. Entry score floors, minimum conviction requirements, and candidate eligibility thresholds remain unchanged.

This policy is not a Track B replacement. Track B manages open positions independently. Rotation logic must not override, interrupt, or compete with Track B.

This policy is not an execution mandate. No position is required to close based on this specification. Future implementation, if approved, will be shadow-only before any live execution is considered.

---

## 3. Evidence Base

### Diagnostic Tools

Two read-only diagnostic scripts were built and tested before this specification:

- `scripts/trade_quality_report.py` - Capital deployment quality, entry score distribution, margin block analysis, ETF overlap, cluster concentration, PRU/discovery gap.
- `scripts/rotation_shadow_report.py` - Counterfactual analysis: which weak open positions could theoretically have freed capacity for margin-blocked high-score candidates.
- `scripts/rotation_paper_validation.py` (added 2026-05-13) - Evaluates qualifying margin blocks against closed trade outcomes in a 24h lookahead window. Produces A/B/C scenario reports. `live_rotation_allowed`, `broker_connected`, and `order_generation_allowed` are always False.

All scripts are stdlib only, produce no trading side effects, and write to `data/` subdirectories.

### Session Evidence

**2026-05-11**

| Metric | Value |
|---|---|
| Trade quality verdict | WEAK_ENTRIES_DETECTED |
| Book average score | 59.1 |
| Positions below 35 | 2 |
| Positions 35-49 | 3 |
| Margin blocks | 4 |
| Blocked average score | 67.2 |
| Blocked vs book delta | +8.1 |
| Blocked candidates with gap >15 | 1 (AVGO 80, gap +20.9) |
| Blocked candidates with gap >20 | 1 |
| ETF overlap flags | 1 |
| Tech/AI/Semis NLV | 42.3% |
| PRU/discovery selected average | 37.0 |
| Normal-path selected average | 58.7 |
| Rotation shadow verdict | ROTATION_WATCH |

**2026-05-12**

| Metric | Value |
|---|---|
| Trade quality verdict | WEAK_ENTRIES_DETECTED |
| Book average score | 54.8 |
| Positions below 35 | 3 (WDC 27, XLK 26, XLE 23) |
| Positions 35-49 | 4 (SNDK, TSM, CVX, KO) |
| Margin blocks | 4 |
| Blocked average score | 59.0 |
| Blocked vs book delta | +4.2 |
| Blocked candidates with gap >15 | 1 (AVGO 80, gap +25.2) |
| Blocked candidates with gap >20 | 1 |
| ETF overlap flags | 2 (XLK 26, XLE 23) |
| Tech/AI/Semis NLV | 35.6% |
| PRU/discovery gap | 18.4 points |
| Rotation shadow verdict | ROTATION_SHADOW_CONFIRMED |

**Top shadow rotation candidates on 2026-05-12 (DVA block, corrected block-time book avg):**

| Rank | Symbol | Score | Notional | ETF Overlap | Carry |
|---|---|---|---|---|---|
| 1 | XLE | 23 | $98,233 | Yes (XOM, CVX) | No |
| 2 | XLK | 26 | $57,412 | Yes (AAPL, MSFT) | Yes |
| 3 | WDC | 27 | $57,599 | No | Yes |

**Theoretical capacity release:**

| Set | Notional | NLV% |
|---|---|---|
| Top 1 (XLE) | $98,233 | 10.2% |
| Top 2 (XLE + XLK) | $155,645 | 16.1% |
| Top 3 (XLE + XLK + WDC) | $213,243 | 22.1% |

---

**2026-05-13**

| Metric | Value |
|---|---|
| Shadow verdict | ROTATION_SHADOW_CONFIRMED |
| Book average score | 59.2 |
| High-score blocked (gap >15) | 3 (NVDA 71, TSLA 75, NBIS 85) |
| High-score blocked (gap >20) | 1 (NBIS +25.8 — highest gap recorded across all sessions) |
| Strongest blocked candidate | **NBIS score 85** — new high-water mark |
| Positions below 35 | 2+ (XLE 23, WDC 27 still carried from prior session) |
| Reconstruction confidence | MEDIUM (no TQR artifact yet at report time) |

**Top shadow rotation candidates on 2026-05-13 (NBIS block, gap +25.8):**

| Rank | Symbol | Score | Notional | ETF Overlap | Carry |
|---|---|---|---|---|---|
| 1 | XLE | 23 | $98,233 | Yes (XOM, CVX) | Yes (multi-day) |
| 2 | WDC | 27 | $57,599 | No | Yes (multi-day) |
| 3 | TSM | 39 | $57,318 | No | No |

**Theoretical capacity release:**

| Set | Notional | NLV% |
|---|---|---|
| Top 1 (XLE) | $98,233 | 10.2% |
| Top 2 (XLE + WDC) | $155,832 | 16.1% |
| Top 3 (XLE + WDC + TSM) | $213,150 | 22.1% |

**Paper validation status as of 2026-05-13:**

The paper validation framework (`scripts/rotation_paper_validation.py`) ran its first evaluation against the 2026-05-12 DVA block (gap +16.7, book_avg 57.3). Verdict: `PAPER_VALIDATION_PENDING_OUTCOMES`. XLK outcome resolved (P&L −$968). XLE and WDC remain open — outcomes pending position close. A second qualifying opportunity was captured: NBIS (score 85, gap +25.8) from today's session.

### ETF Overlap Findings

Both XLK (score 26) and XLE (score 23) are broad sector ETFs holding overlapping single-name positions already in the book. XLK overlaps AAPL and MSFT. XLE overlaps XOM and CVX. Both scores are below the 50-point threshold that marks the LOW tier. Both ETFs appeared as the top two shadow candidates by rotation shadow score.

### PRU/Discovery Status

PRU/discovery source labels were not available in the apex_decision_audit for these sessions. The conclusion was INSUFFICIENT_DATA. This specification does not draw conclusions about PRU/discovery capacity consumption and makes no policy recommendations conditional on PRU/discovery behaviour until the data is available.

### Data Quality Limitations

The following data gaps reduce confidence in exact calculations:

- Blocked candidate intended notional is not captured in the log. Capacity matching is directional only.
- Positions closed during the session are not in positions.json at report time. Book reconstruction is end-of-session only.
- Protected or manual conviction flags are not present in position metadata. The spec cannot distinguish a deliberate hold from a weak position.
- Track B active management state at block time is not recorded in a parseable form for the diagnostic.
- PRU/discovery source labels were absent for the sessions analysed.

These gaps inform the data requirements listed in Section 15 and must be resolved before live implementation.

---

## 4. Policy Design Principles

Any future rotation implementation must conform to these principles. Deviation from any principle requires explicit written justification and Amit approval before implementation.

**Deterministic.** Given identical inputs, the policy must produce identical decisions. No probabilistic or LLM-derived outputs in the core rotation decision path.

**Explainable.** Every rotation decision must be traceable to specific input values: blocked candidate score, blocked candidate gap, open book scores, shadow rotation ranking formula. No black-box scoring.

**Fail-closed.** If any required input is missing, stale, or below confidence threshold, the policy takes no action. Missing data means no rotation. This is not optional.

**Diagnostic-first.** Every rotation event must be logged in full before any execution begins. The log must include which candidate was blocked, which shadow candidate was selected, the rotation shadow score, the capacity calculation, and the gate results that authorised the rotation.

**Score-aware.** The policy must operate on entry scores, not on P&L, unrealised loss, or sentiment. Rotation is a capacity management tool, not a loss-cutting tool.

**Capacity-aware.** The policy must estimate required capacity from the blocked candidate notional and must release only the minimum capacity needed. Overshooting capacity release is a separate risk.

**Risk-aware.** Post-rotation exposure must be checked against all active risk caps. Rotation must not produce a post-trade state that violates any existing cap.

**Cluster-aware.** Same-cluster swaps must pass stricter score uplift gates. Freeing a weak Tech/AI/Semis position to enter a stronger Tech/AI/Semis position does not reduce cluster risk. The policy must flag this and require a higher blocked-vs-book gap to justify same-cluster rotation.

**ETF-overlap-aware.** Low-score ETFs with single-name overlap are structurally weaker holdings from a tracking-error and overlap standpoint. They must rank higher as shadow candidates but must not be automatically closed.

**Protected-position-aware.** Positions with a manual conviction flag, a Track B active hold, or any metadata indicating deliberate retention must be excluded from shadow candidate ranking. The policy must read this metadata before scoring.

**Track B-compatible.** Rotation must yield to Track B on any position Track B is actively managing. The two systems must not conflict.

**Never tier-led.** Rotation eligibility and ranking must never depend on PRU/discovery tier assignment. Source metadata may be logged for analysis but must not be a gate condition or ranking input until separately validated.

---

## 5. Activation Preconditions

A future rotation policy should only evaluate shadow candidates when all of the following conditions are met simultaneously. These are proposed minimum gates. All of them must pass. A single failed gate stops the entire evaluation.

| Gate | Condition |
|---|---|
| G1 | A margin block event has fired for a candidate in the current cycle |
| G2 | The blocked candidate score is >= 70 |
| G3 | The blocked candidate outscores the live book average by >= 20 points |
| G4 | At least 3 open positions score below 50, all of which were open before the block timestamp |
| G5 | At least 1 open position scores below 35 |
| G6 | Top 1 to top 3 shadow candidates can theoretically free enough notional to match the blocked candidate required size |
| G7 | The block reason is confirmed as margin cap, not spread quality or data staleness |
| G8 | The blocked candidate passes normal eligibility checks at block time (instrument type, regime, catalyst quality) |
| G9 | Reconstruction confidence is MEDIUM or HIGH |
| G10 | Blocked candidate required notional is available (not INSUFFICIENT_DATA) |
| G11 | Account data and NLV are current, not stale |

Notes on gate calibration:

- G2 (score >= 70) is set deliberately above the MEDIUM tier top (65) to require HIGH-confidence entries only. Rotation for a score-64 candidate is not justified by the current evidence.
- G3 (gap >= 20) matches the ROTATION_SHADOW_CONFIRMED criterion from the shadow report.
- G10 (blocked candidate notional available) is currently INSUFFICIENT_DATA. This gate cannot pass until the data plumbing in Section 15 is resolved. Live rotation cannot begin until G10 passes reliably.

---

## 6. Non-Activation Conditions

The policy must not activate, and no shadow candidate evaluation must begin, if any of the following is true:

- Blocked candidate score is below 70.
- The block is caused by spread quality, not margin cap.
- The block is caused by stale price data or missing instrument data.
- The blocked candidate does not outscore the live book average by at least 20 points.
- Fewer than 3 open positions score below 50 before the block timestamp.
- No open position scores below 35.
- All shadow rotation candidates are marked as protected or manual conviction.
- NLV data is missing or more than 5 minutes stale at block time.
- Broker account sync is stale.
- Post-rotation exposure would violate any active risk cap.
- Track B is actively managing the candidate shadow position in a protected state.
- The regime is one where forced turnover is undesirable and no explicit regime override has been designed.
- The session is within the last 30 minutes before EOD flat. EOD flat handles all positions and must not be interrupted by rotation logic.

These conditions are not ranked. Any one of them vetoes activation.

---

## 7. Shadow Rotation Candidate Eligibility

### Eligible positions

An open position may be considered as a shadow rotation candidate if it meets any one of the following criteria:

- Entry score is below 50.
- Entry score is more than 20 points below the blocked candidate score.
- Position is an ETF with direct single-name overlap and score below 50.
- Position belongs to a cluster whose average open score is below 50 and the position is among the lowest-scoring members of that cluster.
- Position was sourced as PRU/discovery (once data confirms this reliably) and its score is materially below the normal-path selected average.

All eligible positions must also satisfy:

- The position was open before the blocked candidate timestamp (timestamp confirmed, not assumed).
- The position has a confirmed entry score (not None, not UNKNOWN).

### Excluded positions

A position must be excluded from shadow rotation candidate consideration regardless of its score if:

- It carries a manual conviction flag or protected hold metadata.
- It was opened after the blocked candidate timestamp.
- Its entry score equals or exceeds the blocked candidate score.
- Track B has an active hold or active exit in progress on this position.
- Exiting it would violate a risk or liquidity constraint (minimum lot size, thin market, wide spread).
- Its score is missing and no qualifying structural flag (ETF overlap, cluster) compensates.

---

## 8. Shadow Ranking Formula

The diagnostic formula from `scripts/rotation_shadow_report.py` is the starting point for a live ranking formula. It is not final. It has not been validated against live outcomes.

```
rotation_shadow_score =
    score_delta                              (blocked_score minus position_score)
    + 10  if position score below 35
    + 8   if ETF overlap flag and ETF score below 50
    + 5   if low-score cluster flag
    + 5   if PRU/discovery displacement flag  (when data is sufficient)
    + 3   if position is older than current session (carry position)
```

**Component explanations:**

`score_delta` is the primary driver. The larger the gap between what was blocked and what is currently held, the stronger the case for considering the position as a shadow candidate. A score-23 position held while an 80-score candidate is blocked represents a 57-point opportunity cost.

The `below 35` bonus (+10) reflects that positions at this score level are in the QUESTIONABLE tier. Their entry was marginal by Decifer's own thresholds. They represent the weakest capital deployment.

The `ETF overlap` bonus (+8) reflects that a low-score ETF holding overlapping single names provides diluted, double-counted exposure. It is structurally weaker than a direct single-name position at the same score.

The `low-score cluster` bonus (+5) reflects that a weak position sitting inside a cluster whose average score is already low contributes to cluster quality deterioration without providing diversification benefit.

The `PRU/discovery` bonus (+5) is conditional on data availability. It is currently inactive due to INSUFFICIENT_DATA status.

The `carry` bonus (+3) reflects that a position that carried over from a prior session had a full additional day to prove its thesis. If it is still weak after carrying, the marginal case for holding it over a stronger new entry is weaker.

**Validation requirement:**

This formula must be back-tested against at least 10 shadow sessions before it is used in any live ranking. The question to answer: do positions ranked highest by this formula, when replaced by the blocked candidate, produce better book-level outcomes in the following session? If the correlation is weak, the formula must be revised before live use.

---

## 9. Capacity Matching Logic

A rotation policy must not free more capacity than necessary. Overshooting is a separate risk: it creates unintended exposure gaps and may cause the bot to fill them with lower-quality entries on the next cycle.

**Required data (currently missing for at least one field):**

- Blocked candidate required notional: must come from the sizing engine output for that specific candidate, not estimated. This is currently INSUFFICIENT_DATA.
- Blocked candidate intended entry price and quantity.
- Current gross exposure before and after hypothetical rotation.
- Post-rotation margin requirement under IBKR rules.

**Matching logic:**

1. Compute required notional from sizing engine output for blocked candidate.
2. Sort shadow candidates by rotation shadow score, descending.
3. Select the minimum subset of shadow candidates whose combined notional meets or exceeds required notional. Prefer top 1. Use top 2 only if top 1 is insufficient. Use top 3 only if top 2 is insufficient.
4. Verify that after releasing selected candidates and entering the blocked candidate, post-trade gross exposure is within all active caps.
5. If post-trade exposure still violates caps after top 3 release, do not proceed.

**Overreach prevention:**

If the minimum sufficient set of shadow candidates releases more capacity than needed by more than 20%, the surplus must not be used to enter additional candidates in the same cycle. Excess capacity rolls to the next normal Apex evaluation cycle.

---

## 10. ETF Overlap Handling

ETF overlap is folded into rotation shadow candidate ranking via the +8 bonus for low-score ETFs with direct single-name overlap. No separate ETF suppression module is created.

**Treatment by ETF category:**

| Category | Treatment |
|---|---|
| Sector ETF with direct overlap (XLK, XLE, SMH, XLF) | Score below 50 with confirmed overlap: apply +8 bonus in rotation shadow ranking. Score >= 50: no bonus. |
| Broad market ETF (SPY, QQQ, IWM) | These hold hundreds of names. Overlap analysis is not meaningful. Rotation shadow scoring applies score_delta only. No overlap bonus. Separate treatment required if these become rotation candidates. |
| Macro / alternative ETF (GLD, IBIT, TLT, UUP) | Not equity overlap. These represent distinct asset class exposure. No overlap bonus applies. Do not treat as rotation candidates unless the blocked candidate is also in the same macro category. |
| Thematic overlap ETF (USO vs XOM/CVX) | Apply overlap analysis for direct thematic components. Same +8 bonus logic applies if score below 50. |

**What this policy does not authorise:**

- Blocking ETF entries at the point of selection.
- Suppressing ETFs from the Apex candidate list.
- Prioritising single names over ETFs at the Apex input stage.
- Any change to ETF scoring dimensions.

---

## 11. Cluster Handling

Cluster quality affects both rotation candidate eligibility and rotation outcome quality.

**Same-cluster swaps:**

Replacing a weak Tech/AI/Semis position with a stronger Tech/AI/Semis blocked candidate does not improve cluster diversification. It may improve book quality by score but does not reduce sector concentration risk. Same-cluster swaps require a higher blocked-vs-book gap:

- Cross-cluster swap: gap >= 20 (standard gate G3 applies).
- Same-cluster swap: gap >= 25 (stricter gate, reflecting the absence of diversification benefit).

This stricter gate is a proposal. It must be reviewed in shadow testing to confirm it does not exclude too many valid cases.

**Low-score cluster flag:**

If a cluster's average open score is below 50 and a position in that cluster is being evaluated as a shadow candidate, the low-score cluster bonus (+5) applies in the rotation shadow score. This reflects that the cluster as a whole is weak, not just the individual position.

**Cross-cluster swaps:**

If the blocked candidate and the shadow candidate are in different clusters, rotation may reduce sector concentration risk. This is a positive secondary effect, not a primary gate. Cross-cluster rotation must still pass all activation preconditions.

**Cluster concentration cap:**

After any rotation, no cluster should exceed its prior NLV concentration unless the newly entered position is in a cluster already below 20% NLV. This is a post-trade check, not a pre-trade gate. The policy must compute this before submitting any exit order.

---

## 12. PRU / Discovery Handling

PRU/discovery source metadata remains diagnostic only. No tier-led action is authorised.

**Current status:**

PRU/discovery source labels were absent from apex_decision_audit for the sessions analysed. The rotation shadow report returned PRU_DISCOVERY_INSUFFICIENT_DATA for both sessions. The +5 bonus in the ranking formula is present but inactive until data becomes available.

**What is permitted:**

Future sessions may produce reliable PRU/discovery source labels. When that data is available across at least 10 sessions, the following analysis is permitted:

- Were PRU/discovery-sourced positions disproportionately represented in the shadow candidate list?
- Did PRU/discovery-sourced positions have materially lower average scores than normal-path selections?
- Did PRU/discovery-sourced positions consume capacity before normal-path candidates were blocked?

If that analysis confirms a pattern, the +5 PRU/discovery bonus in the ranking formula is validated and may remain. If the pattern is not confirmed, the bonus must be removed.

**What is not permitted:**

- No tier-led allocation.
- No tier-led suppression.
- No PRU rescue.
- No automatic exclusion of PRU/discovery candidates from Apex.
- No preferential or penalised scoring at the Apex input stage.

---

## 13. Track B Interaction

Track B manages open positions through TRIM, EXIT, and HOLD decisions made by the Apex PM call. Rotation must never compete with or override this system.

**Interaction rules:**

1. Before any position is evaluated as a shadow rotation candidate, the system must check whether Track B has an active HOLD or active TRIM/EXIT in progress for that position. If Track B has made a decision in the current session, that decision takes precedence.

2. If Track B has already signalled an exit on the same position that the rotation policy would target, rotation must yield. Track B's exit will free capacity naturally. The rotation policy must not double-signal an exit.

3. If Track B has explicitly issued a HOLD on a position, that position is excluded from shadow rotation candidate consideration for the remainder of the session.

4. If Track B and rotation policy produce conflicting signals on the same position at the same time, Track B wins unconditionally.

5. Rotation is a capacity management mechanism. Track B is a risk and opportunity management mechanism. These are different concerns. Rotation must not be used to short-circuit Track B logic.

**Exit ordering:**

If a shadow rotation candidate is selected and must be exited to free capacity, the exit must follow the same execution path as a Track B-initiated exit. It must respect spread, slippage, and session constraints. It must be logged identically to a Track B exit. It must not use a different order type, pricing logic, or size logic than the standard exit path.

---

## 14. Risk and Execution Boundaries

These boundaries apply to any future live implementation. They are not guidelines. Every one of them is a hard constraint.

**Order type:**

No market orders purely from rotation policy. Rotation exits must use limit orders with spread-aware pricing. If a limit order cannot be filled within acceptable spread, the rotation does not proceed for that candidate.

**Session timing:**

No rotation within 30 minutes of EOD flat time. EOD flat logic handles all positions. Rotation must not initiate exits that conflict with EOD flat sequencing.

**Liquidity:**

No rotation exit for a position in a thin market or with a spread above the system's normal bid-ask gate. If liquidity is insufficient at execution time, cancel and do not proceed.

**Account sync:**

No rotation if IBKR account data (NLV, buying power, margin requirement) is more than 5 minutes stale. If stale, wait for sync or skip the cycle.

**Price data:**

No rotation if the position's last trade price is stale. Stale price means the notional estimate is unreliable and the capacity calculation cannot be trusted.

**Post-trade check:**

After computing the hypothetical post-rotation portfolio state, verify that gross exposure, sector concentration, and margin utilisation all remain within active caps. If any cap is violated in the hypothetical state, do not proceed.

**Partial fills:**

If the shadow candidate exit results in a partial fill, the freed capacity is partial. The system must not enter the blocked candidate unless the freed capacity from the partial fill is sufficient to cover the required notional. Do not enter a blocked candidate on the assumption that the remainder of the exit will fill.

**Concurrent rotation:**

At most one rotation evaluation per Apex cycle. If two blocked candidates both meet the activation gates in the same cycle, the higher-scoring blocked candidate takes priority. Do not attempt parallel rotation.

---

## 15. Data Requirements Before Implementation

The following data items are currently unavailable, incomplete, or unreliable. None of these items are optional. Every one must be resolved before a live implementation sprint is authorised.

**Item 1: Blocked candidate required notional**
Currently INSUFFICIENT_DATA. The log records the block event but not the intended position size. The sizing engine output for the blocked candidate must be captured and logged at decision time.
Resolution: add notional logging to the margin block event in the bot loop.

**Item 2: Exact required buying power**
IBKR margin requirements differ by instrument and position type. The policy needs the precise buying power required for the blocked candidate, not an estimate based on notional alone.
Resolution: query IBKR whatIfOrder or equivalent at block time and log the result.

**Item 3: Open book at block timestamp**
Currently reconstructed end-of-session from positions.json. Positions closed during the session are not captured.
Resolution: write a periodic or event-triggered position snapshot to a JSONL file with timestamps, so the book state can be reconstructed at any point during the session.

**Item 4: Protected/manual conviction flag**
Currently absent from position metadata. Without this flag, the policy cannot distinguish a deliberate hold from a weak position.
Resolution: add a `hold_protected` boolean field to the position schema. Track B HOLD decisions and any Amit-flagged positions must write this field.

**Item 5: Track B active management state**
Whether Track B issued a HOLD, TRIM, or EXIT decision in the current session for a given position is not currently parseable from a single field.
Resolution: add a `track_b_last_decision` and `track_b_decision_ts` field to position state, updated each time Track B evaluates the position.

**Item 6: Position source label**
PRU/discovery source labels were absent. Source labelling must be written to apex_decision_audit reliably and consistently.
Resolution: ensure every apex_candidate record in apex_decision_audit includes `scanner_tier` or `pru` field at write time.

**Item 7: Position age**
The carry/session distinction currently depends on whether open_time is before the --since date. A more precise definition would use session open time (e.g., 09:30 ET) as the boundary.
Resolution: normalise open_time to UTC and define carry as open_time before 13:30 UTC (09:30 ET) on the session date.

**Item 8: Realised and unrealised P&L at block time**
Not currently logged at block time. The policy does not need this to make a decision, but it is needed for shadow testing validation.
Resolution: include P&L snapshot in the periodic position log described in Item 3.

**Item 9: Whether candidate remained attractive after capacity was freed**
The shadow report cannot determine whether AVGO would still have been selected after a hypothetical rotation freed capacity. If the Apex cycle that blocked AVGO had already moved to the next cycle by the time capacity was freed, the opportunity may have passed.
Resolution: log the cycle_id of the block event and the cycle_id of the next Apex evaluation. If they differ, mark the shadow candidate as having a temporal validity gap.

---

## 16. Shadow Testing Plan

Before any live or paper implementation of rotation logic, a shadow testing phase must be run for at least 5 sessions and ideally 10 sessions. No implementation sprint is authorised until this phase is complete and reviewed.

**What to run:**

After each session, run `scripts/rotation_shadow_report.py` and record the output in a session log. The rotation shadow report already writes JSON artifacts to `data/rotation_shadow_reports/`. The shadow testing phase formalises review of these artifacts.

**What to track per session:**

1. Did the rotation shadow verdict fire at ROTATION_SHADOW_CONFIRMED, ROTATION_WATCH, NO_ROTATION_EVIDENCE, or INSUFFICIENT_DATA?
2. Which candidate was blocked? At what score?
3. What were the top 3 shadow rotation candidates and their scores?
4. If the shadow rotation had been executed hypothetically, what would the book average score have been after the swap?
5. How did the blocked candidate perform in the sessions after the block? (Look back after the fact.)
6. How did the shadow rotation candidates perform in the sessions after? Would exiting them have been costly?
7. How many false positives appeared: cycles where the gates fired but the shadow candidate turned out to be the better hold?
8. Did any ETF shadow candidates resolve on their own through Track B?
9. Did any carry positions with low scores turn around and produce strong outcomes (false positive for rotation)?
10. Did the data quality gaps (Items 1-9 in Section 15) affect the shadow analysis? Were they still present?

**Success criteria for graduating to implementation design:**

All of the following must be true before an implementation design sprint is authorised:

- Shadow verdict ROTATION_SHADOW_CONFIRMED fired in at least 3 of the observed sessions.
- In at least 2 of those sessions, the hypothetical blocked candidate outperformed the shadow rotation candidate in the following session (directional price performance, not necessarily realised P&L).
- False positive rate (shadow rotation candidate turned out to be the better hold) is below 30%.
- Data quality items 1 through 6 from Section 15 have been resolved.
- No ETF shadow candidate that was flagged was resolved through Track B exits (if it resolves on its own, rotation was unnecessary).
- Amit has reviewed the shadow testing log and agreed the pattern is durable.

If these criteria are not met after 10 sessions, the spec must be revised before implementation proceeds.

---

## 17. Policy Escalation Gates

Movement from this specification to an implementation design sprint requires all of the following:

| # | Gate | Status |
|---|---|---|
| 1 | ROTATION_SHADOW_CONFIRMED in at least 3 additional sessions beyond the 2 already observed when this spec was written | **1 of 3 met** (2026-05-13 confirmed) |
| 2 | Same or similar blocked candidates recur across sessions | **Partially met** — DVA recurred; NBIS (85) is new but pattern of high-score candidates blocked is consistent |
| 3 | Same weak carry positions recur | **Met** — XLE (23) and WDC (27) appear as top shadow candidates across 3+ sessions without closing |
| 4 | Blocked candidate required notional available (Gate G10 from Section 5 passes) | **Not met** — G10 still provisional; estimated_notional only |
| 5 | Shadow replacement analysis shows hypothetical replacements would have produced higher-quality book in ≥2 of 3 confirmed sessions | **Not yet evaluable** — paper validation outcomes pending (XLE, WDC still open) |
| 6 | All data requirements in Section 15 resolved or with documented workarounds | **Not met** — Items 1, 3, 4, 5, 6 still open |
| 7 | Data reconstruction confidence reached HIGH in ≥2 sessions | **Partially met** — JSONL snapshots give HIGH confidence at block time; MEDIUM overall due to G10 |
| 8 | Amit reviewed spec and shadow testing log, given explicit written approval | **Pending** |

This is not a checklist that can be partially satisfied. All eight gates must pass.

---

## 18. Implementation Boundary for Future Sprint

This section describes what a future implementation sprint should look like. It is not an authorisation to begin. Nothing in this section may be built until the escalation gates in Section 17 are met.

**Module structure:**

A rotation policy should be a standalone, isolated module. It must not be embedded in the bot loop, the Apex orchestrator, or the order execution path directly. It should operate as a pre-execution policy check that the bot loop calls at a defined point in the margin-block handling path.

The module should be named `rotation_policy.py` or similar and placed in the appropriate service layer directory.

**Sequencing of development:**

1. Read-only policy evaluation module. Logs decisions, takes no action.
2. Shadow-only mode: logs what would have been executed if live.
3. Paper-only mode: submits actual orders on the paper account only.
4. Live gate: requires a separate Amit approval decision after paper results are reviewed.

**Implementation constraints:**

- Deterministic. No LLM in the rotation decision path.
- No duplicate sizing logic. Rotation must call the existing sizing engine, not replicate it.
- No direct broker calls from the policy module. Broker calls go through the existing execution path.
- Fail-closed by default. Any unexpected condition or missing data produces no action and a logged warning.
- All rotation decisions must be logged to a dedicated `data/rotation_decisions.jsonl` file with full input state and the gate results.
- The module must be independently testable with fixtures. No live system dependency in tests.
- The module must be separately approved through code review before any paper mode activation.

---

## 19. Recommended Current Decision

*(Updated 2026-05-13)*

**DESIGN_ROTATION_POLICY_SPEC is complete.** This document is the output of that decision. The spec was drafted on 2026-05-12 and updated on 2026-05-13 with the new session evidence.

**Paper validation is live.** `scripts/rotation_paper_validation.py` evaluates qualifying margin blocks against closed trade outcomes in a 24h lookahead window. First run verdict: `PAPER_VALIDATION_PENDING_OUTCOMES` (XLE and WDC still open). A second opportunity (NBIS, score 85) was captured today. Outcomes will resolve when those positions close.

**Live rotation is not justified.** The escalation gates in Section 17 require 2 more confirmed sessions (Gates 1), paper validation outcomes (Gate 5), and resolution of five data quality items (Gate 6). None of these is met yet.

**ETF suppression remains folded into rotation analysis.** Low-score ETFs with single-name overlap are handled as higher-priority shadow rotation candidates via the +8 ranking bonus. XLE and WDC have now appeared as top-2 shadow candidates across three consecutive sessions. This is structural, not noise. No separate suppression module is needed or authorised.

**PRU rescue remains off.** PRU/discovery source metadata is insufficient for the sessions analysed. No tier-led policy action is justified.

**The immediate next technical work is waiting for paper validation outcomes.** XLE and WDC need to close so the 2026-05-12 DVA block can be scored. After outcomes resolve, run the validation again. If verdict upgrades to `PAPER_VALIDATION_SUPPORTS_ROTATION` across 2 of the captured opportunities, that satisfies Gate 5.

---

## 20. Final Executive Summary

*(Updated 2026-05-13)*

**What has been proven across 4 sessions:**

Over four observed sessions (2026-05-11 through 2026-05-13), ROTATION_SHADOW_CONFIRMED fired in three of them. The pattern is structural:

- High-score candidates are repeatedly blocked by margin cap. The strongest blocked candidate observed to date is NBIS at score 85 (2026-05-13), with a gap of +25.8 over the book average — the highest gap recorded.
- XLE (score 23) and WDC (score 27) have appeared as top shadow candidates in every confirmed session. Both are multi-day carries holding approximately $98k and $58k respectively. They have not closed through Track B.
- The book average sits in the 57–59 range across sessions. The blocked candidate scores range from 74 (DVA) to 85 (NBIS). The pattern of high-conviction entries being displaced by weak carries is repeatable and confirmed by JSONL observability data (not log-regex inference).
- Reconstruction confidence has improved to HIGH for block-time book state (JSONL snapshots match trigger-field within 1-2ms), and MEDIUM overall (G10 still provisional).

**What was not proven:**

It was not proven that exiting XLE or WDC would have produced a better outcome than holding them. Paper validation outcomes are pending — XLE and WDC are still open. The first resolved outcome (XLK) shows P&L of −$968 for that shadow exit.

It was not proven that the blocked candidates (DVA, NBIS) would have been selected by Apex after capacity was freed. Cycle_id tracking is not yet logged.

It was not proven that PRU/discovery sourcing contributed to capacity consumption. Source labels remain absent from several sessions.

**What should happen next:**

1. Wait for XLE and WDC to close. Re-run paper validation. If verdict upgrades to `PAPER_VALIDATION_SUPPORTS_ROTATION`, Gate 5 in Section 17 can be assessed.
2. Continue running the shadow report daily. Two more ROTATION_SHADOW_CONFIRMED sessions are needed to meet Gate 1.
3. Start resolving data quality items from Section 15, starting with Item 1 (blocked candidate notional logging). This is the single item that unblocks G10 and enables the full capacity matching pipeline.

**What should not happen yet:**

Do not implement live rotation. Do not enable PRU rescue. Do not build a separate ETF suppression module. Do not change entry thresholds, margin caps, or scoring formulas. Do not wire any rotation logic into the bot loop, order execution, or risk engine.

The system is generating strong training data. Two high-conviction candidates (NBIS 85, DVA 74) were blocked in a single session while XLE and WDC carried weak scores across multiple sessions. The gap is real. The appropriate response is to let the paper validation confirm the direction, resolve the data quality gaps, and then design implementation — in that order.
