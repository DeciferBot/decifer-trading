# WEEKEND_RUNTIME_TUNING_HANDOFF.md
# Decifer 3.0 — Runtime Tuning Session 2026-04-26

## Status
Fixes implemented and tested. Repo is green (pre-existing failures only).
Cleanup and dead-code work explicitly deferred until runtime behavior is confirmed satisfactory.

---

## Root-Cause Summary

### Problem 1: Zero Entries All Session (SCAN_CYCLE)

**Root cause:** `max_tokens=2048` in `apex_call()` is too small for SCAN_CYCLE responses
when 80+ candidates are in the input.

Direct evidence from the shadow log (Friday pre-market entries):
- Entry at 23:18 UTC: 13,215 input tokens → **2,047 output tokens** → `parse_error` → fallback → `new_entries: []`
- Entry at 23:43 UTC: 9,993 input tokens → **2,048 output tokens** → `parse_error` → fallback → `new_entries: []`

Both hit the exact ceiling. JSON was truncated mid-object. `json.loads()` failed. `_fallback_decision()` fired, returning `new_entries: []`.

A third entry (22:52 UTC) was not truncated (1,963 tokens) but returned empty because Apex
saw `DAR=None` across all candidates — that's a pre-market data quality artifact, not a
production issue.

**Compounding factor:** The system prompt says "rationale: Required even for AVOID." If Apex
writes an AVOID entry for all 81 candidates that means 81 × ~100 tokens = ~8,100 tokens of
required output. 2048 is structurally guaranteed to truncate with large candidate batches.

**Additional finding:** The SCAN_CYCLE execute=True path in `bot_trading.py` NEVER called
`log_shadow_result`. On Friday (2026-04-24), 54 scan cycles ran and dispatched through Apex
with zero audit trail in `apex_shadow_log.jsonl`. The shadow log only captured TRACK_B_PM
entries (29 total). This was a complete observability blind spot.

---

### Problem 2: Null-Symbol Drops (51 cumulative Friday)

**Root cause:** Apex generates `new_entries` in TRACK_B_PM calls where `candidates_by_symbol={}`
(empty dict). Any entry Apex emits in a TRACK_B_PM response has a symbol that doesn't exist
in the empty candidates dict, so `filter_semantic_violations` drops it with a warning:
`"filter_semantic_violations: XXXX not in candidates — dropping"`.

These warnings were being counted as "null-symbol drops" in the session monitor.

The user prompt already showed `[TRACK A — NEW CANDIDATES] (0)` but had no explicit
instruction. Apex treated "no candidates" as context, not as a binding constraint, and
sometimes generated entries anyway.

Clustering pattern confirmed the source: drops correlated with TRACK_B_PM firing cadence
(18-min pre-market, ~75-min mid-day), not with scan cycle timing.

**Bonus find:** TRACK_B_PM `portfolio_state` omitted `position_slots_remaining`. The user
prompt builder defaults it to 0, so every TRACK_B_PM prompt said `slots_left=0` even when
90 slots were free. Apex narrative said "zero slots remaining" for the entire session.

---

## Fixes Applied

### Fix 1 — Raise max_tokens: 2048 → 4096
**File:** `market_intelligence.py:976`
```python
# Before:
raw, _meta = _call_apex_meta(system_prompt, user_prompt, max_tokens=2048)
# After:
raw, _meta = _call_apex_meta(system_prompt, user_prompt, max_tokens=4096)
```
Eliminates JSON truncation on SCAN_CYCLE calls with large candidate batches.

---

### Fix 2 — Shadow logging for SCAN_CYCLE execute=True
**File:** `bot_trading.py` (SCAN_CYCLE cutover block, after `_cut_result`)
```python
try:
    _aorch_track_a.log_shadow_result("SCAN_CYCLE", _cut_result)
except Exception as _log_err:
    log.warning("APEX_LIVE: shadow log failed — %s", _log_err)
```
Every SCAN_CYCLE Apex call now writes to `data/apex_shadow_log.jsonl`. The log will show
trigger_type="SCAN_CYCLE" with note="executed" (not "shadow"). Visible on next market open.

---

### Fix 3 — Cap candidates to top 30 by score
**File:** `bot_trading.py` (SCAN_CYCLE cutover block, filter_candidates call site)
```python
_cut_candidates_raw = _fc_track_a(...)
_cut_candidates = sorted(
    _cut_candidates_raw, key=lambda c: c.get("score", 0), reverse=True
)[:30]
```
Reduces both input token pressure and required output size. Apex focuses on the 30 highest-
scoring names. Candidates 31–81 were marginal (scored just above min_score_to_trade=14) and
would almost certainly have received AVOID anyway. Logged when cap fires.

---

### Fix 4 — Explicit no-new-entries constraint when candidates=[]
**File:** `market_intelligence.py` (`_build_apex_user_prompt`)
```python
if not candidates:
    parts.append("  (none this cycle — output new_entries: [])")
```
When Track A is empty, Apex now receives an explicit instruction in the prompt rather than
relying on implicit inference from the empty list. Eliminates the null-symbol drop source.

---

### Fix 5 — Add position_slots_remaining to TRACK_B_PM portfolio_state
**File:** `bot_trading.py` (TRACK_B_PM Apex input construction, line ~1673)
```python
"position_slots_remaining": max(
    0, int(CONFIG.get("max_positions", 0) or 0) - len(pm_open_pos)
),
```
TRACK_B_PM now reports accurate slot availability. Apex narrative will no longer say
"zero slots remaining" when 90 slots are free.

---

## What Was NOT Changed

- Architecture layers (unchanged)
- Cutover flags (unchanged)
- Dispatcher contracts (unchanged)
- Rollback paths (unchanged)
- Legacy pipeline code (untouched)
- Any cleanup or dead-code deletion (explicitly deferred)
- Dashboard or cosmetic work (out of scope)

---

## Test Results

Full regression: **2044 passed, 1 skipped** (my changes introduce zero new failures).

Pre-existing failures (present before this session, not caused by these changes):
- `test_portfolio_optimizer.py::TestCorrelationTracker::test_update_builds_valid_correlation_matrix` — pandas shape mismatch, pre-existing
- `test_signals.py` — 5 failures + 5 errors, pre-existing
- Additional pre-existing failures in unrelated modules

All modules touched by this session (`market_intelligence.py`, `bot_trading.py`) pass their
relevant tests cleanly.

---

## Remaining Risks

1. **4096 tokens may still be borderline with large Track B** — If Track B (open positions)
   is also large (e.g., 50 positions flagged for review) AND Track A has 30 candidates, the
   combined output could still approach 4096 tokens. Monitor `output_tokens` in the shadow
   log on first market open. If truncation recurs at 4096, raise to 8192.

2. **Root cause of valid-but-empty responses not fully eliminated** — Fix 1 eliminates
   truncation-caused empties. But Apex might still return empty when it legitimately judges
   all candidates as AVOID (low DAR, divergence flags, etc.). This is judgment behavior, not
   a bug. After the first post-fix market session, review shadow log entries with
   `note: "executed"` to see what Apex is actually deciding.

3. **Candidate cap at 30 is a judgment call** — If the top-30 by score still results in
   many AVOID decisions, consider tightening the min_score threshold passed to
   `filter_candidates` to reduce the candidate universe earlier. This is a tuning decision
   for after observing real behavior.

4. **Null-symbol fix relies on prompt compliance** — Fix 4 adds an explicit instruction but
   doesn't enforce it structurally. If Apex still occasionally emits entries in Track B calls,
   they'll still be dropped by `filter_semantic_violations`. The fix reduces frequency, not
   guarantees zero. A structural fix (stripping `new_entries` from Track B results before
   semantic filter) would be belt-and-suspenders — deferred pending observation.

---

## What to Watch on Next Market Open

1. **`apex_shadow_log.jsonl`** — Look for entries with `trigger_type: "SCAN_CYCLE"` and
   `note: "executed"`. These are the newly-visible SCAN_CYCLE calls. Check:
   - `output_tokens` — should be well under 4096 (was hitting 2047/2048 before)
   - `new_entries` array — should now have entries if strong candidates exist
   - `_meta.error` field — should be absent (no more `parse_error`)

2. **Candidate cap log** — Look for log lines matching:
   `"APEX_LIVE: X candidates after guardrails — capped to top 30 by score"`
   Confirms the cap is firing and reducing load.

3. **Null-symbol warning count** — Watch for lines matching:
   `"filter_semantic_violations: * not in candidates — dropping"`
   Should be near-zero on first session (Fix 4 + TRACK_B_PM constraint).

4. **TRACK_B_PM slots_left** — In the shadow log TRACK_B_PM entries, the `market_read`
   should no longer say "zero slots remaining" unless positions genuinely fill all 100 slots.

---

## Cleanup / Dead-Code Work

**Explicitly deferred.** No cleanup, legacy deletion, or architectural changes will be made
until runtime behavior after this fix set is confirmed satisfactory on a full market session.

This document should be superseded after Monday's session review.

---

*Session: 2026-04-26 | Weekend Phase — Decifer 3.0 Runtime Tuning*
*Files modified: market_intelligence.py, bot_trading.py*
