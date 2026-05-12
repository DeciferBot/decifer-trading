# Rotation Shadow Report — Design Doc

**Created:** 2026-05-12  
**Author:** Cowork (Claude)  
**Status:** Active — diagnostic-only, shadow observation phase  

---

## Purpose

The Rotation Shadow Report is a read-only counterfactual diagnostic that answers one question:

> If a high-score candidate was blocked by margin, which weak open positions would have been the most logical shadow rotation candidates, and how much capacity could they theoretically have freed?

It does **not** recommend any position be sold, rotated, or managed differently. It does not change trading behaviour. It is a diagnostic tool that generates evidence to support a future policy decision.

---

## Why This Exists

The Trade Quality Report (TQR) identified a two-session pattern ending 2026-05-12:

| Date | Book Avg | Entries <35 | Margin Blocks | Blocked >15 over book | Verdict |
|---|---|---|---|---|---|
| 2026-05-11 | 59.1 | 2 | 4 | 1 (AVGO 80) | WEAK_ENTRIES_DETECTED |
| 2026-05-12 | 54.8 | 3 | 4 | 1 (AVGO 80, gap +25.2) | WEAK_ENTRIES_DETECTED |

Both sessions showed:
- 2–3 positions below score 35 consuming 15–18% NLV
- 3–4 positions in the LOW (35–49) tier consuming a further 25–27% NLV
- AVGO scoring 80 blocked by margin in both sessions
- Low-score ETF positions (XLE 23, XLK 26) overlapping held single names
- PRU/discovery selected average (34.6) materially below normal-path average (53.0)

The decision gate moved from KEEP OBSERVING to BUILD ROTATION SHADOW REPORT NEXT because:

1. Weak carry-book entries **repeatedly** blocked stronger later candidates across two consecutive sessions.
2. Day 2 produced a stronger signal: AVGO at 80 blocked, exceeding both the >15 and >20 thresholds.
3. The pattern was not ambiguous — every sub-metric worsened between the two sessions.

---

## What the Script Does

`scripts/rotation_shadow_report.py` runs offline after a session and:

1. **Parses margin block events** from `logs/decifer.log` with their exact timestamps.
2. **Reconstructs the open book** at each block timestamp using `open_time` from `data/positions.json`.
3. **Identifies shadow rotation candidates** — open positions that could theoretically have freed capacity.
4. **Ranks them** using a transparent, deterministic formula (Section 3).
5. **Estimates theoretical capacity release** for top 1, 2, and 3 candidates (Section 4).
6. **Flags ETF overlap** within the shadow candidate set (Section 5).
7. **Reports cluster quality** issues in the rotation set (Section 6).
8. **Diagnoses PRU/discovery contribution** to capacity consumption (Section 7).
9. **Answers 7 counterfactual questions** in plain language (Section 8).
10. **Returns a computed shadow verdict** (Section 9).

---

## What the Script Does NOT Do

- Does not recommend selling any position.
- Does not recommend rotating any position.
- Does not wire into the bot loop, order flow, risk engine, or any execution system.
- Does not change thresholds, scoring, or selection logic.
- Does not enable PRU rescue or tier-led allocation.
- Does not produce trade signals of any kind.
- Does not modify any source data files.

Every finding in the report uses the language "shadow rotation candidate" and "theoretical capacity release". These are diagnostic labels, not execution instructions.

---

## Two-Session Evidence Summary

### 2026-05-11 (Rotation Shadow Report verdict: ROTATION_WATCH)

- AVGO (80) blocked. Gap vs book average: +20.9 pts — crossed the >20 threshold.
- Shadow rotation candidates before block: XLE (23), XLK (26), WDC (27), TSM (39), CVX (44), KO (47), CEG (59).
- Top 1 theoretical release: $57,504 (6.0% NLV).
- Top 3 theoretical release: $173,519 (18.1% NLV).
- Multi-session flag: False (single session at that point).
- Verdict: ROTATION_WATCH — one session, threshold met, run one more.

### 2026-05-12 (Rotation Shadow Report verdict: ROTATION_SHADOW_CONFIRMED)

- AVGO (80) blocked again. Gap vs book average: +25.2 pts — both >15 and >20.
- Shadow rotation candidates: XLK (26, carry), XLE (23), WDC (27, carry), TSM (39, carry), CVX (44, carry), KO (47), CEG (59, carry).
- Top 1 shadow candidate: XLK, $56,920.
- Top 3 theoretical release: $173,519 (18.1% NLV).
- Multi-session flag: True (prior TQR session confirmed WEAK_ENTRIES_DETECTED with outscores_15=1).
- Verdict: **ROTATION_SHADOW_CONFIRMED**.

All four ROTATION_SHADOW_CONFIRMED gates passed:
1. Multi-session pattern: ✅ (two sessions of WEAK_ENTRIES_DETECTED with blocked candidates >15 over book)
2. At least one blocked candidate >20 over book: ✅ (AVGO +25.2)
3. At least three weak positions <50 before block: ✅ (7 candidates found)
4. Top-3 theoretical NLV release is material (>$40K): ✅ ($173,519)

---

## Ranking Formula

The shadow rotation score is transparent and deterministic:

```
rotation_shadow_score =
    score_delta (blocked_score − position_score)
    + 10  if position score below 35
    + 8   if ETF overlap flag and ETF score below 50
    + 5   if low-score cluster flag
    + 5   if PRU/discovery displacement flag
    + 3   if position is older than current session (carry)
```

This formula is tested in `tests/test_rotation_shadow_report.py::TestRankingFormula`.

---

## Shadow Verdict Definitions

| Verdict | Criteria | Recommended Action |
|---|---|---|
| `NO_ROTATION_EVIDENCE` | No blocked candidate outscored book by >15; few/no weak open positions | KEEP OBSERVING |
| `ROTATION_WATCH` | ≥1 blocked >15 over book; ≥2 weak positions; single session | RUN_ONE_MORE_SESSION |
| `ROTATION_SHADOW_CONFIRMED` | Multi-session; ≥1 blocked >20 over book; ≥3 weak positions; top-3 NLV release material; confidence MEDIUM/HIGH | DESIGN_ROTATION_POLICY_SPEC |
| `INSUFFICIENT_DATA` | Missing positions, scores, block data, or notional | FIX_DATA_QUALITY |

---

## Safety Boundaries

This script obeys strict production isolation rules:

**Imports only:** stdlib (no `pandas`, `numpy`, no trading runtime modules).

**Does not import:**
- `bot_ibkr.py`, `orders_core.py`, `orders_state.py`
- `market_intelligence.py`, `apex_orchestrator.py`
- `bot_trading.py`, `bot_dashboard.py`
- `config.py`, `risk_manager.py`, `guardrails.py`
- Any execution, position sizer, or signal dispatcher module

**Reads only (never writes):**
- `data/positions.json`
- `data/apex_decision_audit.jsonl`
- `data/tier_d_funnel.jsonl`
- `data/trade_quality_reports/*.json`
- `logs/decifer.log`

**Writes only to:**
- `data/rotation_shadow_reports/report_<UTC>.json`
- `data/rotation_shadow_reports/report_<UTC>.txt`
- stdout

---

## How to Run

**Standard usage (run from repo root):**
```bash
python3 scripts/rotation_shadow_report.py --since 2026-05-12
```

**With explicit repo root (e.g., from a worktree):**
```bash
python3 scripts/rotation_shadow_report.py \
    --since 2026-05-12 \
    --repo-root "/Users/amitchopra/Desktop/decifer trading"
```

**With custom output directory:**
```bash
python3 scripts/rotation_shadow_report.py \
    --since 2026-05-12 \
    --output-dir /tmp/rsr
```

**Default `--since`:** today in UTC.  
**Runs in:** under 10 seconds.  
**Tolerates:** missing files, malformed JSONL lines, incomplete data.

---

## How to Interpret the Report

### Section 0 — Header
Check: how many blocks were detected, how many were high-score (gap >15), and what the reconstruction confidence is.

### Section 1 — Blocked Candidate Summary
The definitive list of margin-blocked candidates with their scores and gap vs book average. Focus on rows marked `>15 ⚑` or `>20 ⚑⚑` — these are the missed opportunities.

### Section 2 — Open Book at Time of Block
Shows exactly which positions were open when the block occurred. The `CARRY` column distinguishes positions that entered before the current session from same-day entries. ETF overlap flags are shown inline.

### Section 3 — Shadow Rotation Candidate Ranking
The ranked list of positions that could theoretically have freed capacity. The higher the `RSR_SCORE`, the stronger the shadow rotation case. This is NOT a sell recommendation — it is a diagnostic ranking.

### Section 4 — Theoretical Capacity Release
How much NLV the top 1, 2, or 3 shadow candidates would theoretically free. Blocked candidate notional is always `INSUFFICIENT_DATA` (not available from logs) — compare the theoretical release to typical position sizes (~$55–95K each).

### Section 5 — ETF Overlap Within Rotation
Low-score ETFs that also overlap single-name positions appear here. The `REPEAT` column tracks whether the same ETF appeared as a flag in prior sessions.

### Section 6 — Cluster Quality
Which clusters contain shadow rotation candidates. The `swap_within_cluster` flag means freeing that candidate would not reduce cluster concentration — it would just swap one position for another in the same sector.

### Section 7 — PRU/Discovery
Whether PRU/discovery-sourced positions contributed to capacity consumption. `PRU_DISCOVERY_CAPACITY_CONSUMPTION_WATCH` means PRU positions appeared as shadow candidates in the same session that high-score candidates were blocked.

### Section 8 — Counterfactual Summary
Seven questions answered directly: strongest missed opportunity, weakest consumers, recurring weak symbols, theoretical release by top 1/2/3, root cause, and whether live rotation is justified. The answer to question 7 is always: **No live rotation yet. Shadow evidence only.**

### Section 9 — Shadow Verdict
The computed verdict. If `ROTATION_SHADOW_CONFIRMED` fires, the next step is to draft a rotation policy specification — not to wire rotation into live execution.

---

## What Would Justify Moving to Rotation Policy Specification

The gate for `ROTATION_SHADOW_CONFIRMED` is:
1. Multi-session pattern confirmed across ≥2 sessions.
2. At least one blocked candidate outscored book by >20 points.
3. At least three weak positions below 50 existed before the block.
4. Top-3 theoretical NLV release is material (>$40K).
5. Reconstruction confidence is MEDIUM or HIGH.

**All five gates were met as of 2026-05-12.** The next logical step is `DESIGN_ROTATION_POLICY_SPEC` — a design document specifying what a rotation policy would look like, what its rules are, what constraints it must obey, and how it would be tested in shadow mode before any live wiring.

**ROTATION_SHADOW_CONFIRMED does not mean live rotation is approved.** It means the evidence is sufficient to write a policy specification. Amit must review and approve the specification before any runtime implementation begins.

---

## Test Coverage

`tests/test_rotation_shadow_report.py` — 64 tests covering:

| Test Class | Tests | What's Covered |
|---|---|---|
| `TestBlockedCandidateParsing` | 5 | Margin blocks extracted, spread blocks excluded, deduplication, gap thresholds |
| `TestBookReconstruction` | 8 | Before/after/at-ts filtering, null timestamp, confidence HIGH/MEDIUM/LOW |
| `TestShadowCandidateEligibility` | 6 | Score <50, delta >20, ETF overlap, high-score exclusion, missing score |
| `TestRankingFormula` | 7 | Each bonus independently, all bonuses stacked, determinism, carry bonus |
| `TestCapacityRelease` | 4 | Top 1/2/3, empty candidates, NLV missing, pct_nlv |
| `TestCandidateNotionalMissing` | 1 | INSUFFICIENT_DATA label with theoretical release still populated |
| `TestETFOverlapInRotation` | 4 | Low-score ETF eligible, high-score ETF excluded, section_5 detection, repeat flag |
| `TestClusterQuality` | 2 | Tech cluster low score, swap_within_cluster flag |
| `TestPRUDiscovery` | 4 | Capacity consumption watch, rotation watch, no tier language, not relevant |
| `TestVerdictThresholds` | 8 | All four verdicts, all gate conditions, action mapping |
| `TestMissingFiles` | 4 | Log/positions/jsonl all missing, full report with no data |
| `TestMalformedJSONL` | 4 | Malformed counted, all-malformed, empty file, malformed positions |
| `TestFullReportIntegration` | 8 | End-to-end with fixtures, artifacts written, AVGO blocked, no trading language |

Run: `python3 -m pytest tests/test_rotation_shadow_report.py -v`
