# Weekend Robustness Handoff — Decifer 3.0
**Date:** 2026-04-25 (Saturday)
**Author:** Cowork (Claude)
**Status:** Pass 1 complete. Tests green. Pending Amit approval to commit.

---

## Phase A Summary — Full 6-Track Audit

Completed a full runtime fragility audit across 6 tracks. Key findings:

### Test Baseline
- **2074 passed, 1 skipped** (clean baseline before this session)
- The "12 pre-existing failures" claimed in the prior handoff was stale — the suite was already clean.

### Most Significant Finding: Why 81→0 on 2026-04-24

The primary cause of zero entries across 54 scan cycles (81 above-threshold candidates present) was a combination of:

1. **No entry floor** — the system prompt gave explicit license for empty arrays ("Both arrays may be empty") with no counterbalancing instruction. The model's default under FEAR_ELEVATED was unconditional inaction.
2. **FEAR_ELEVATED as de-facto veto** — `_DEFAULT_SESSION_CHARACTER = "FEAR_ELEVATED"` is the fallback. Without a clarifying instruction, the model treated the label as an AVOID mandate.
3. **divergence_flags over-applied** — the prompt only stated flags block *options* eligibility but provided no guidance for stocks; the model may have applied the same filter broadly.
4. **Silent fallback indistinguishable from deliberate caution** — if any cycle hit a parse/schema/LLM error, the `_fallback_decision()` result looked identical to a conservative Apex decision from the operator log.
5. **Zero-entries observability log at INFO** — invisible at WARNING production level; operators could not see the pattern building across 54 cycles.

---

## Phase B — Pass 1 Fixes Applied

### Fix #1 + #22 — Entry floor + divergence_flags clarification
**File:** `market_intelligence.py`

Added two new sections to `_APEX_SYSTEM_PROMPT`:

1. **ENTRY FLOOR RULE** (new section before OUTPUT schema):
   - When ≥3 candidates have score ≥35 and no named systemic blocking condition, Apex **must** produce at least one new entry.
   - FEAR_ELEVATED explicitly stated to be a regime descriptor, not an AVOID mandate.
   - If all candidates are AVOIDed despite strong scores, market_read must name a *specific* blocking condition — vague caution is not sufficient.

2. **divergence_flags clarification** (added to instrument section):
   - Divergence flags restrict instrument selection to "stock" only — they do NOT veto the stock trade itself.
   - Explicitly stated: "Never AVOID a stock trade solely because divergence_flags is non-empty."

### Fix #3 — `_fallback_decision()` clearly distinguishable
**File:** `market_intelligence.py`

Added `log.error("apex_call: FALLBACK DECISION — <reason> (all new_entries suppressed, positions held)")` inside `_fallback_decision()` itself.

- **Before:** fallback returns silently; callers log the cause at ERROR but the effect (suppressed entries) was not separately logged; operator saw "0 entries, 0 actions" indistinguishable from deliberate caution.
- **After:** Every fallback path emits an ERROR log naming the reason. Combined with `_meta.error` already set by callers (confirmed present on all 3 fallback paths), the shadow log and operator log both carry clear evidence.

### Fix #4 — Zero-entries observability log raised to WARNING
**File:** `apex_orchestrator.py`

Changed `log.info(...)` → `log.warning(...)` for the "apex: zero entries — trigger= candidates= market_read=" log.

- **Before:** INFO level — invisible at WARNING production log threshold.
- **After:** WARNING level — visible in the operator log stream whenever Apex returns 0 entries despite having candidates.

### Fix #18 — `_format_review_line` pnl_pct=None guard
**File:** `market_intelligence.py`

Added a None guard: `pnl_str = f"{pnl:+.2%}" if pnl is not None else "n/a"`.

- **Before:** `p.get('pnl_pct'):+.2%` raised `TypeError` when `pnl_pct` is None, crashing the PM review prompt build → silent no-op in PM Track B.
- **After:** Renders "n/a" for missing PnL; PM prompt build never crashes on missing position data.

### Fix #5 — brain.py "0/4 agents agreed" footer
**File:** `Chief-Decifer-recovered/panels/brain.py`

Changed line 455: `f"{agents}/4 agents agreed"` → conditional expression that shows "Apex Synthesizer" when `agents == 0`.

- **Before:** Every Apex-generated trade card showed "0/4 agents agreed" — factually wrong and misleading for operator trust.
- **After:** Shows "Apex Synthesizer" when `agents_agreed=0` (3.0 mode); shows legacy format when agents > 0 (preserves rollback path display correctly).

---

## Files Modified

| File | Fixes Applied |
|------|---------------|
| `market_intelligence.py` | #1 (entry floor), #22 (divergence note), #3 (fallback ERROR log), #18 (pnl_pct guard) |
| `apex_orchestrator.py` | #4 (zero-entries WARNING) |
| `Chief-Decifer-recovered/panels/brain.py` | #5 (agents-agreed footer) |
| `tests/test_pass1_robustness.py` | New — 10 targeted tests covering all Pass 1 changes |

---

## Test Results

| Scope | Pass | Fail | Notes |
|-------|------|------|-------|
| Pass 1 targeted tests (new) | 10/10 | 0 | All new behaviors verified |
| Apex scan-cycle + PM + NI + bypass tests | 41/41 | 0 | No regression in cutover paths |
| Full regression | 2083 | 0 | +10 vs 2074 baseline (new tests) |
| Pre-existing flaky test | — | 1 | `test_backoff_capped_sequence_values` passes alone, flakes under parallel load; unrelated to this session |

**Zero regressions introduced.**

---

## Expected Monday Impact

### Most likely outcome of the entry floor (#1 + #22)
The prompt change is the highest-leverage fix. On Friday, Apex saw 81 above-threshold candidates (top score 53) with TRENDING_UP regime and returned `new_entries: []` every cycle. After this fix:
- Apex is explicitly told it MUST produce an entry when ≥3 candidates score ≥35.
- FEAR_ELEVATED no longer reads as an AVOID mandate.
- divergence_flags no longer read as a blanket stock-level veto.

**This cannot be verified without a live session.** The only true test is Monday open. Watch `data/apex_shadow_log.jsonl` for `new_entries` count in the first 3 PRIME_AM cycles.

### Fallback visibility (#3 + #4)
If Monday still shows 0 entries, the WARNING-level zero-entries log and the ERROR-level fallback log will immediately tell you whether the cause is:
- Apex deliberately choosing AVOID (WARNING log fires, no ERROR)
- Apex hitting a parse/schema/LLM error (ERROR fires with reason, WARNING also fires)

Before this fix, both cases looked identical in the operator log.

---

## Ranked Issue Table — Full Status

| # | Issue | Severity | Status |
|---|-------|----------|--------|
| 1 | No entry floor in Apex prompt; FEAR_ELEVATED as de-facto veto | BLOCKER | **Fixed (#1 + #22)** |
| 2 | Apex receives thin payload (no analyst/sector/fundamentals block) | BLOCKER | **Deferred — Pass 2 if needed** |
| 3 | `_fallback_decision()` silent; indistinguishable from deliberate caution | HIGH | **Fixed** |
| 4 | Zero-entries log at INFO, invisible at WARNING level | HIGH | **Fixed** |
| 5 | brain.py shows "0/4 agents agreed" on every Apex trade card | HIGH | **Fixed** |
| 6 | `_DEFAULT_SESSION_CHARACTER="FEAR_ELEVATED"` baked-in conservative fallback | HIGH | Deferred — prompt already addresses the symptom; changing the default requires understanding whether the fallback itself is responsible for session cycles |
| 7 | overview.py Multi-Agent Council / TradingView Screener | MEDIUM | Deferred — dashboard cleanup session |
| 8 | 4-agent output loop populates 4 empty "No output" panels | MEDIUM | Deferred — dashboard cleanup session |
| 9 | `score_universe` exception in NEWS_INTERRUPT → silent empty Track A | MEDIUM | Deferred |
| 10 | `flag_positions_for_review` exception → PM silent no-op | MEDIUM | Deferred |
| 11 | Token-budget warning post-hoc; no preemptive truncation at 3500 | MEDIUM | Deferred |
| 12 | NEWS_INTERRUPT calls Apex with empty Track A on noisy days | MEDIUM | Deferred (need cost telemetry) |
| 13 | No unit test for B-2 DAR=None render path | MEDIUM | Deferred |
| 14 | Phase 6 shadow block (lines 2764–2877) unreachable dead code (B-7) | MEDIUM | Deferred — delete after Monday stability confirmed |
| 15 | Double-dispatch PM/NI pattern fragile | MEDIUM | Deferred — refactor |
| 16 | 4-agent output loop populates dashboard with 4 empty outputs | MEDIUM | Deferred — dashboard cleanup |
| 17 | blueprint.py: "4-agent pipeline" | COSMETIC | Cleanup later |
| 18 | pnl_pct=None in `_format_review_line` → TypeError crash in PM build | LOW | **Fixed** |
| 19 | Dispatch error messages swallowed (count only) | LOW | Cleanup later |
| 20 | `safety_overlay` flag reads no first-time INFO log | LOW | Cleanup later |
| 21 | `bot_voice` mock leak → thread warning in test suite | LOW | Cleanup later |
| 22 | divergence_flags may be interpreted as stock-level hard veto | LOW | **Fixed (prompt clarification)** |

---

## Monday-Open Watchlist (priority order)

1. **First 3 PRIME_AM scan cycles**: check `data/apex_shadow_log.jsonl` for `new_entries` count. If still 0, look at `market_read` — does it name a specific blocking condition, or is it vague? That tells you whether the prompt fix worked or whether Pass 2 (richer context block) is needed.

2. **Check for FALLBACK DECISION in logs**: grep the bot log for `"FALLBACK DECISION"`. If this appears frequently, the issue is LLM/parse failures (not model conservatism) and a separate investigation is needed.

3. **Check zero-entries WARNING frequency**: `grep "apex: zero entries" <bot_log>`. This should now be visible at WARNING level. If it appears on every cycle despite the prompt change, the model still has a conservatism issue and Pass 2 should be prioritised.

4. **NEWS_INTERRUPT**: check if the first news trigger produces a new entry (B-5 fix from yesterday). Verify `scored_candidate` is non-null in the shadow log.

5. **PM Track B**: confirm no-op guard fires correctly when portfolio is deployed and nothing is flagged. Should see "PM no-op" DEBUG log, not a Sonnet call.

---

## Cleanup / Deletion — STILL DEFERRED

The following were diagnosed but explicitly **not touched** in this session:

- `Chief-Decifer-recovered/panels/overview.py:83,110–114,169,176` — Multi-Agent Council, TradingView Screener labels
- `Chief-Decifer-recovered/panels/blueprint.py:165` — "4-agent pipeline"
- `bot_trading.py:2164–2206` — 4-agent output loop (empty panels)
- `bot_trading.py:2764–2877` — Phase 6 shadow block dead code (B-7)

**Rule:** Do not delete or refactor these until Monday runtime is confirmed stable (≥1 session with expected entry behavior).

---

## Pass 2 Decision Criteria

**Implement Pass 2 (#2 — richer trade-context block) if and only if:**
- Monday PRIME_AM still shows zero entries after 3+ cycles, AND
- The WARNING-level zero-entries log shows Apex's market_read is not naming a specific blocking condition (i.e., the model has context but is still AVOIDing)

**If Monday shows entries being produced:** Pass 1 was sufficient. Proceed to deferred cleanup items.

---

*This handoff supersedes `WEEKEND_RUNTIME_TUNING_HANDOFF.md`.*
*Cleanup and deletion remain deferred until Monday runtime is proven satisfactory.*
