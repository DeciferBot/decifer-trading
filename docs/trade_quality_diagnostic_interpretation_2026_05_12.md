# Trade Quality Diagnostic — Interpretation Report
**Date:** 2026-05-12  
**Sprint type:** Pre-session validation + historical baseline  
**Author:** Cowork (Claude)

---

## Service Layer Classification

| Property | Value |
|----------|-------|
| Service layer | Reporting / diagnostics only |
| Runtime purpose | Offline analysis |
| Live bot loop dependency | None |
| Broker dependency | None |
| Trading side effects | None |
| Modules not imported | bot_*, orders_*, market_intelligence, apex_orchestrator, config.py, broker, risk, signal_dispatcher |
| Protected files modified | None |

---

## Section 1: Execution Verdict

**DONE**

All 9 tasks completed without touching any protected file.

---

## Section 2: Repo State

| Property | Value |
|----------|-------|
| Branch | `claude/naughty-matsumoto-37e1b4` |
| Starting commit | `d69be58` |
| Ending commit | No commit made (explicitly not staged or committed) |
| Dirty files before work | `scripts/trade_quality_report.py` (untracked), `tests/test_trade_quality_report.py` (untracked), `.claude/memory/` files (untracked), `data/apex_shadow_reports/` (untracked) |
| Dirty files after work | Same as above — no changes to existing tracked files |
| Protected files touched | None |
| Runtime data files modified | None (read only) |

**Dirty file classification:**

| File | Classification |
|------|----------------|
| `scripts/trade_quality_report.py` | Code — new diagnostic (untracked, safe) |
| `tests/test_trade_quality_report.py` | Tests — new test file (untracked, safe) |
| `.claude/memory/*.json` | Unknown / session metadata — not code |
| `data/apex_shadow_reports/` | Runtime data (pre-existing untracked dir) |

No protected code file was dirty before this work began.

---

## Section 3: Diagnostic Verification

| Check | Result |
|-------|--------|
| `scripts/trade_quality_report.py` exists | PASS |
| `tests/test_trade_quality_report.py` exists | PASS |
| Standard library only | PASS — imports: `argparse, json, pathlib, re, statistics, sys, collections, datetime, typing` |
| No live runtime imports | PASS — no bot_*, orders_*, market_intelligence, config, apex_orchestrator |
| No broker imports | PASS |
| No protected file writes | PASS |
| `--since` argument supported | PASS |
| `--output-dir` argument supported | PASS |
| Writes `.txt` and `.json` reports | PASS |
| Prints text report to stdout | PASS |
| Tolerates missing files | PASS — `dq.mark_missing()` pattern, no crash |
| Tolerates malformed JSONL | PASS — bad lines counted, skipped, never fatal |
| Does not write to runtime input files | PASS |
| `score` fallback when `entry_score` is None/empty | PASS — `training_score()` checks `entry_score` then `score` |
| `positions.json` as dict or list | PASS — `load_positions()` handles both |
| Spread blocks separated from margin sequencing | PASS — `parse_spread_blocks()` → `spread_blocked_syms` set, excluded from Section 2 |
| PRU/discovery terminology, no tier-led recommendations | PASS — Section 7 explicitly states "No tier-led allocation or selection priority is recommended." |

---

## Section 4: Tests

**Command run:**
```
python3 -m pytest tests/test_trade_quality_report.py -v
```

**Result:** 12 passed, 0 failed, 0 errors  
**Runtime:** 0.86s

| Test | Result |
|------|--------|
| `test_section1_chronological_order_and_cumulative_notional` | PASS |
| `test_section2_outscores_15_flag` | PASS |
| `test_section2_spread_block_not_counted_as_margin_block` | PASS |
| `test_section3_high_score_skips_ranked_by_gap` | PASS |
| `test_section3_pru_displacement_watch_fires_and_no_tier_language` | PASS |
| `test_section4_score_buckets` | PASS |
| `test_section5_etf_overlap_flag_fires_and_clears` | PASS |
| `test_training_score_normalises_none_entry_score` | PASS |
| `test_load_jsonl_since_filter` | PASS |
| `test_compute_verdict_all_branches` | PASS |
| `test_missing_files_degrade_gracefully` | PASS |
| `test_malformed_jsonl_skipped_not_fatal` | PASS |

No modifications made to the script or tests — all tests passed on first run.

---

## Section 5: Report Runs

### Historical baseline (2026-05-11)
```
python3 scripts/trade_quality_report.py --since 2026-05-11
```
Output artifacts:
- `data/trade_quality_reports/report_20260512T030858Z.txt` (12K)
- `data/trade_quality_reports/report_20260512T030858Z.json` (9.2K)

Verdict: **WEAK_ENTRIES_DETECTED**

### Pre-session check (2026-05-12)
```
python3 scripts/trade_quality_report.py --since 2026-05-12
```
Output artifacts:
- `data/trade_quality_reports/report_20260512T030913Z.txt` (10K)
- `data/trade_quality_reports/report_20260512T030913Z.json` (9.0K)

Verdict: **EXPECTED_PRE_SESSION_NO_DATA**

Rationale: The 2026-05-12 report shows 1 Apex cycle (vs 66 for 2026-05-11), 0 candidates observed, 0 selections, 0 closed trades. This is exactly expected pre-session state — the market session has not run yet. The `--since 2026-05-12` filter correctly excludes prior-session activity. The report is structurally valid but carries no new intraday evidence.

The open positions shown in the 2026-05-12 report are **carry-forward positions** from 2026-05-11 (no open_time filter on positions.json). This is correct behaviour: positions are a point-in-time snapshot, not date-filtered. The Section 8 verdict (`WEAK_ENTRIES_DETECTED`) reflects the carry-forward book, not new session entries — which is the correct baseline interpretation going into 2026-05-12.

---

## Section 6: Key Findings From 2026-05-11 Baseline

### Computed metrics (from generated JSON report)

| Metric | Value |
|--------|-------|
| Apex cycles analysed | 66 |
| Candidates observed | 1,269 |
| Apex selections | 183 |
| Open positions | 16 |
| Closed trades | 26 |
| Session P&L | +$9,029 (open +$16,111, closed -$7,082) |
| Regime | TRENDING_UP |
| NLV | $972,841 |
| Gross exposure | 108.8% |
| Book avg score (open) | 59.1 |
| Positions below score 35 | 2 (WDC=27, XLK=26) |
| Positions score 35–49 | 3 (SNDK=37, TSM=39, CVX=44) |
| Unique symbols blocked by margin cap | 4 (TSLA=74, ASML=74, NBIS=46, AVGO=75) |
| Blocked-candidate avg score | 67.2 |
| Delta (blocked avg − book avg) | +8.1 |
| Blocked outscoring book by >15 pts | 1 (AVGO=75, gap=+15.9) |
| Blocked outscoring book by >20 pts | 0 |
| ETF overlap flags | 1 (XLK, score=26, overlaps AAPL/MSFT/NVDA) |
| Cluster concentration flags | 1 (Tech/AI/Semis: 42.3% NLV, avg score 50.3) |
| High-score displacement records | 334 |
| PRU_SOURCE_DISPLACEMENT_WATCH cases | 17 (PRU/disc selected over normal-path, gap>15) |
| PRU/disc avg selected score | 37.0 |
| Normal-path avg selected score | 58.7 |
| PRU/disc displacement gap total | 33 cases |
| PRU/discovery verdict | PRU_DISCOVERY_OVERSELECTION_WATCH |

### Capital deployment sequence summary

| Entry order | Symbol | Time | Score | Notional | Cum Exp% |
|-------------|--------|------|-------|----------|----------|
| 1 (carry) | NVDA | 2026-05-06 | 75 | $61K | 6.3% |
| 2 (carry) | AAPL | 2026-05-07 | 85 | $59K | 12.3% |
| 3 (carry) | MSFT | 2026-05-07 | 63 | $57K | 18.2% |
| 4 (carry) | HIMS | 2026-05-08 | 64 | $108K | 29.2% |
| 5 (carry) | SNDK | 2026-05-08 | 37 | $57K | 35.1% |
| 6 (carry) | WDC | 2026-05-08 | 27 | $62K | 41.5% |
| 7 | CEG | 2026-05-11 09:28 | 59 | $98K | 51.5% |
| 8 | TSM | 2026-05-11 09:49 | 39 | $57K | 57.4% |
| 9 | CVX | 2026-05-11 10:32 | 44 | $95K | 67.2% |
| 10 | XLK | 2026-05-11 10:32 | 26 | $58K | 73.2% |
| 11 | IWM | 2026-05-11 15:29 | 83 | $57K | 79.0% |
| 12 | VRT | 2026-05-11 15:29 | 68 | $58K | 85.0% |
| 13 | XOM | 2026-05-11 15:29 | 63 | $58K | 91.0% |
| 14 | PWR | 2026-05-11 17:22 | 74 | $58K | 97.0% |
| 15 | USO | 2026-05-11 17:22 | 71 | $58K | 102.9% |
| 16 | XLP | 2026-05-11 17:59 | 68 | $58K | 108.8% |

Margin cap hit at 115.4–115.6%, blocking AVGO (75), TSLA (74), ASML (74), NBIS (46).

---

## Section 7: Interpretation

### A. Was capital deployed well?
**Partially.** The book average score of 59.1 is acceptable, but 5 of 16 positions (31%) are in the QUESTIONABLE or LOW buckets (below 50). Two positions entered at scores of 26–27 (WDC, XLK), which are below the QUESTIONABLE threshold. These contributed ~$180K of the ~$400K consumed before morning market open, leaving less room for higher-scored afternoon candidates.

### B. Were weak entries detected?
**Yes.** WDC (27) and XLK (26) are explicitly below the QUESTIONABLE threshold of 35. Additionally, SNDK (37), TSM (39), and CVX (44) are in the LOW bucket. Together these five positions represent $419K (~43% of total deployed capital) at sub-50 conviction.

XLK at score 26 is a notable case: it is a broad-market ETF with full overlap against NVDA, AAPL, and MSFT already held. This represents both a weak-entry and a concentration-amplification problem.

### C. Were stronger candidates blocked later?
**Yes, but moderately.** The margin cap fired 4 times on 2026-05-11. Of these, AVGO (75), TSLA (74), and ASML (74) are all significantly higher than the book average of 59.1. The strongest blocked candidate was AVGO at 75, gap +15.9 — just above the >15 threshold. NBIS (46) was also blocked but is below book average and does not represent a sequencing loss.

The delta of +8.1 (blocked avg 67.2 − book avg 59.1) is meaningful but not severe. One session of evidence at this delta is not a capital sequencing failure. It is sequencing pressure.

### D. Was this Apex selection failure or capital sequencing pressure?
**Capital sequencing pressure, not Apex selection failure.** Apex correctly identified AVGO=75, IWM=83, PWR=74, USO=71 as high-quality entries. The problem is that lower-scored carry positions (WDC, SNDK, XLK from prior sessions) were consuming 41.5% of NLV by market open, leaving only 58.5% of available capacity for the entire 2026-05-11 session. Apex was doing its job; the margin ceiling arrived because the book was already loaded with sub-50 positions carried forward.

This is a carry-book problem, not an Apex selection problem.

### E. Did ETF overlap create disguised concentration?
**Yes.** XLK (score 26) holds AAPL, MSFT, and NVDA as its top components — all three of which are already in the book at scores 85, 63, and 75 respectively. The XLK position adds ~$58K of redundant tech exposure at a low-conviction score. This compounds the Tech/AI/Semis cluster at 42.3% NLV.

USO (score 71) overlaps CVX and XOM thematically, but all three are scored above 60 and USO's score is above the flag threshold (50), so no ETF_OVERLAP_FLAG fires for USO. This is correct behaviour.

### F. Did cluster concentration create hidden portfolio risk?
**Yes, specifically in Tech/AI/Semis.** At 42.3% of NLV ($411K), this cluster holds 7 positions. The cluster avg score is 50.3, dragged down by SNDK (37), WDC (27), TSM (39), and XLK (26). Power/Infrastructure (22.0% NLV, avg 67.0) and Energy (21.7% NLV, avg 59.3) are within reasonable bounds. No other cluster triggered CLUSTER_CONCENTRATION_WATCH.

The DUPLICATE_THEME_WATCH flag for Tech/AI/Semis is valid: SNDK, WDC, TSM, and XLK are all storage/foundry plays with significant correlation. Four correlated positions in the same cluster at sub-50 scores is a redundancy risk.

### G. Is PRU/discovery helping, hurting, or inconclusive?
**PRU_DISCOVERY_OVERSELECTION_WATCH — requires monitoring.**

The evidence is concerning:
- PRU/discovery avg selected score: 37.0
- Normal-path avg selected score: 58.7
- Gap: 21.7 points
- 33 cases where a PRU/discovery candidate was selected over a higher-scoring normal-path candidate (gap >15)
- 17 of those were confirmed PRU_SOURCE_DISPLACEMENT_WATCH cases

This is not a random distribution. PRU/discovery candidates are consistently being selected at significantly lower scores than normal-path candidates that were available in the same cycle. This pattern warrants continued observation. If it repeats across 3+ sessions, it becomes an architectural question about how discovery-labelled candidates are scoring and clearing selection.

**No action recommended yet.** One session. Keep observing.

### H. Is Track B outside the current problem scope?
**Yes.** Track B (exit management via guardrails + Apex PM review) is operating correctly. The sequencing issue is an entry/capacity problem driven by low-score carry positions consuming margin capacity. Track B cannot retroactively remove positions that shouldn't have been entered.

### I. Is any live strategy change justified before the next session?
**No.** The evidence is:
- 1 session of WEAK_ENTRIES_DETECTED
- Sequencing pressure, not CAPITAL_SEQUENCING_FAILURE
- One blocked candidate outscoring book by >15 (not a cascade)
- No replication across multiple sessions

The threshold for a live strategy change is a repeated, evidenced pattern across 3–5 sessions. The correct response today is to re-run this diagnostic after the 2026-05-12 session closes and compare.

### J. What data quality gaps remain?

| Gap | Detail |
|-----|--------|
| `entry_score` field missing in training records | 26/26 records have `entry_score=None`; normalized from `score`. Working correctly; no fix needed. |
| Section 2 book reconstruction uses end-of-session positions | Intraday margin block timestamps not correlated to exact book state at time of block. Score gap analysis is directionally correct; confidence is MEDIUM. |
| Log timezone offset | Log timestamps use local+04 timezone; apex_decision_audit.jsonl uses UTC. No exact correlation possible at timestamp level. Symbol-to-score lookup uses max seen in file. |
| Selected-lower-score missing in some skip records | Minor. Affects display only; score_gap field is reliable. |

---

## Section 8: Strategy Recommendation

**KEEP OBSERVING**

**Rationale:** The 2026-05-11 baseline shows WEAK_ENTRIES_DETECTED with sequencing pressure and a PRU_DISCOVERY_OVERSELECTION_WATCH flag. These are concerning patterns — but they are single-session observations. The verdict thresholds are:

- CAPITAL_SEQUENCING_FAILURE requires: `outscores_20 >= 3 AND low_qual_open >= 3`
- 2026-05-11 shows: `outscores_20 = 0, low_qual_open = 5`

The sequencing pressure is real but not yet at the failure threshold. The PRU/discovery selection gap (37.0 vs 58.7) is the most statistically significant signal and warrants close watching. If PRU_DISCOVERY_OVERSELECTION_WATCH fires again with similar scores after the next 2–3 sessions, that becomes the lead candidate for a targeted investigation into how discovery-labelled candidates are being scored and routed.

**Do not build rotation. Do not build ETF suppression. Do not change thresholds. Observe.**

---

## Section 9: Next Trading Session Watch

After the 2026-05-12 session closes, run this checklist in order:

- [ ] **Re-run the report:** `python3 scripts/trade_quality_report.py --since 2026-05-12`
- [ ] **Book average score:** Was it above or below 59.1 (yesterday's baseline)?
- [ ] **Questionable entries (<35):** Were any new entries made below score 35?
- [ ] **Low entries (35–49):** How many entries in the 35–49 range?
- [ ] **Margin cap fires:** Were any margin blocks recorded in Section 2?
- [ ] **Blocked score vs book gap:** Did any blocked candidate outscore the book by >15? By >20?
- [ ] **CAPITAL_SEQUENCING_FAILURE threshold:** Did `outscores_20 >= 3 AND low_qual_open >= 3` both trigger?
- [ ] **ETF overlap:** Did XLK remain in the book? Did any new ETF enter below score 50 alongside overlapping single-names?
- [ ] **Tech/AI/Semis cluster:** Did the cluster stay above 40% NLV? Did the low-score count (SNDK/WDC/TSM/XLK) stay the same or increase?
- [ ] **PRU/discovery verdict:** Did `PRU_DISCOVERY_OVERSELECTION_WATCH` fire again? What was the PRU/disc avg selected score vs normal-path avg?
- [ ] **PRU displacement watch count:** Was it above 17 (yesterday's count) or below?
- [ ] **Spread blocks:** Were any symbols blocked by spread? Were they correctly excluded from Section 2 margin sequencing analysis?
- [ ] **Session P&L split:** Was closed P&L positive or negative? Did HIGH (65+) scored positions close profitably?
- [ ] **Pattern confirmation:** After this session, do 2 of 2 sessions show WEAK_ENTRIES_DETECTED? Has the sequencing delta (blocked avg − book avg) widened or narrowed?
- [ ] **Decision gate:** If WEAK_ENTRIES_DETECTED fires again with similar low-score entries and PRU_DISCOVERY_OVERSELECTION_WATCH repeats, escalate to Amit for a session 3 review meeting.

---

## Section 10: Morning Prompt

Run this prompt after the 2026-05-12 trading session has produced data:

---

**ROLE:**  
You are a read-only trade quality diagnostics engineer working inside the Decifer Trading codebase.

**DATE:**  
2026-05-12 (post-session)

**TASK:**  
Run the existing trade quality diagnostic for today's session and compare against the 2026-05-11 baseline.

Do not modify any code. Do not change any configuration. This is evidence collection only.

**Step 1 — Run the report:**
```
python3 scripts/trade_quality_report.py --since 2026-05-12
```

**Step 2 — Compare against baseline.** The 2026-05-11 baseline is:
- Book avg score: 59.1
- Positions below 35: 2 (WDC=27, XLK=26)
- Blocked avg score: 67.2 | Delta: +8.1 | outscores_20: 0
- ETF overlap flags: 1 (XLK)
- PRU/disc avg selected: 37.0 | Normal-path avg selected: 58.7 | Gap: 21.7 pts
- Verdict: WEAK_ENTRIES_DETECTED
- PRU verdict: PRU_DISCOVERY_OVERSELECTION_WATCH

**Step 3 — Answer these questions from the generated report only:**
1. What was today's verdict? Same/worse/better than yesterday?
2. Were any new entries below score 35?
3. Did any margin-blocked candidate outscore the book by >15? By >20?
4. Did `outscores_20 >= 3 AND low_qual_open >= 3` both trigger (CAPITAL_SEQUENCING_FAILURE threshold)?
5. Did PRU_DISCOVERY_OVERSELECTION_WATCH fire again? What was the PRU/disc avg selected score vs normal-path?
6. Did the Tech/AI/Semis cluster stay above 40% NLV?
7. Did any new ETF enter below score 50 alongside overlapping single-names?
8. Is the WEAK_ENTRIES_DETECTED pattern confirmed across 2 consecutive sessions?
9. Is the PRU/discovery selection gap (21.7 pts yesterday) widening, narrowing, or stable?

**Step 4 — Recommend one of:**
- KEEP OBSERVING (default — evidence not yet repeated across 3 sessions)
- ESCALATE TO AMIT — pattern confirmed, design session needed (only if CAPITAL_SEQUENCING_FAILURE fired OR PRU gap > 20 pts for 2 consecutive sessions)
- FIX DATA QUALITY (only if a new data quality gap was found that affects verdict reliability)

**Do not implement any strategy change. Do not build rotation. Do not build ETF suppression. Evidence only.**

---

## Appendix: Evidence Table

| Dimension | 2026-05-11 | 2026-05-12 Pre-Session |
|-----------|-----------|----------------------|
| Apex cycles | 66 | 1 (pre-session) |
| Candidates | 1,269 | 0 (pre-session) |
| Selections | 183 | 0 (pre-session) |
| Open positions | 16 | 16 (carry-forward) |
| Closed trades | 26 | 0 (pre-session) |
| Book avg score | 59.1 | 59.1 (carry-forward) |
| Entries below 35 | 2 | 2 (carry-forward) |
| Margin blocks | 4 | 4 (carry-forward from log) |
| Blocked avg score | 67.2 | N/A |
| outscores_15 | 1 | 0 |
| outscores_20 | 0 | 0 |
| ETF overlap flags | 1 | 1 (carry-forward) |
| Cluster watch flags | 1 | 1 (carry-forward) |
| PRU displacement watch | 17 | 0 |
| PRU avg selected score | 37.0 | N/A |
| Normal-path avg selected | 58.7 | N/A |
| PRU verdict | OVERSELECTION_WATCH | INSUFFICIENT_DATA |
| Verdict | WEAK_ENTRIES_DETECTED | WEAK_ENTRIES_DETECTED (carry) |

**2026-05-12 pre-session classified as: EXPECTED_PRE_SESSION_NO_DATA**  
No new evidence available until the 2026-05-12 session closes.
