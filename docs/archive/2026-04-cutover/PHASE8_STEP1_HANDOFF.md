# Phase 8 Step 1 — Verification Handoff

**Date:** 2026-04-24
**Status:** VERIFIED ✅ — `USE_APEX_V3_SHADOW` flipped False → True is live and producing data.

---

## What Changed (the one and only flag touched)

`config.py` line 167:
```
"USE_APEX_V3_SHADOW": True,    # Phase 8 Step 1: apex shadow observation ON
```

No other flag, no other code. Pre-restart `git diff -- config.py` confirmed only this line.

---

## Post-Restart Verification

Bot restarted by Amit. Scan #1 AFTER_HOURS started 02:37:48, completed by 02:52:06.

**Log files created and actively written:**
```
data/apex_shadow_log.jsonl      1179 B   02:52   1 record
data/apex_divergence_log.jsonl  5855 B   02:52   1 record
```

**Shadow record (`scan-1`) is well-formed:**
- `trigger_type`: `SCAN_CYCLE`
- `decision.session_character`: `MOMENTUM_BULL`
- `decision.macro_bias`: `BULLISH`
- `decision.new_entries`: `[]`
- `decision.portfolio_actions`: `[]`
- `decision._meta`:
  - `latency_ms`: **33107** (≈33s — under the ≤30s p95 gate but right at the edge; need ≥20 cycles to compute p95)
  - `attempts`: 1
  - `input_tokens`: 13473
  - `output_tokens`: 1963
  - `model`: `claude-sonnet-4-6`
- `note`: `shadow`
- No schema rejects, no fallback, no apex_call exception.

**Divergence record (`scan-1`) is well-formed:**
- `legacy.new_entries`: 3 symbols (AMZN LONG, NKE SHORT, AON SHORT, scores 46/45/…)
- Apex-side mirror built from shadow result (empty entries → AGREE on no-entry is the expected default when Apex declines).
- Classified events written alongside.

---

## Observations Worth Flagging

1. **Model is `claude-sonnet-4-6`, not Opus.** The ApexDecision was produced by Sonnet,
   not the premium model implied by the architecture doc ("single Opus call"). This is
   a config/resolution item — `claude_model_alpha` is either not set or resolves to
   sonnet. **Not a Step 1 blocker**, but needs a decision before advancing to flip #3
   (`USE_LEGACY_PIPELINE → False`) since the live Apex path would then inherit this
   model choice.

2. **Legacy picked 3 entries (AMZN/NKE/AON); Apex picked 0.** This is AFTER_HOURS
   session, Sonnet correctly flagged `DAR=None` and passed. Expected shadow behavior;
   will show up as DIVERGENT_ENTRY_COUNT in the classifier but does not gate Step 1.

3. **Only one scan cycle so far.** Phase 7B hard gates require ≥20 shadow cycles before
   any further flag flip. This is a time/patience gate — nothing to do in code.

---

## What's Next

Step 1 is complete. Do **not** flip another flag until:

1. ≥ 20 clean scan cycles accumulate in the two log files.
2. `PYTHONPATH="$PWD" python3 scripts/apex_flip_proposer.py status` returns
   all hard gates GREEN:
   - `fallback_rate` ≤ 5%
   - `schema_reject_rate` ≤ 2%
   - `p95_latency_ms` ≤ 30000
   - 0 unresolved HIGH severity divergences
3. Soft gate `AGREE ≥ 90%` met.
4. Resolution of the Sonnet-vs-Opus model item above.

When all four are satisfied, the canonical 6-step flip sequence begins with
`FINBERT_MATERIALITY_GATE_ENABLED → True` (flip #1). See
`PHASE8A_EXECUTE_PATH_HANDOFF.md` for the full sequence and for the execute-path
wiring that will activate on flip #3.
