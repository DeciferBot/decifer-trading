# Decifer 3.0 Runtime Verification

**Documented:** 2026-04-24  
**Session:** Post-cutover monitoring (bot started 08:50 +04, market CLOSED at time of writing)  
**Verifier:** Cowork (Claude)

---

## 1. Final Flag State

All 6 Phase 8 cutover flags are confirmed at Decifer 3.0 values:

| Flag | Value | Meaning |
|------|-------|---------|
| `USE_LEGACY_PIPELINE` | `False` | Apex owns all execute paths |
| `USE_APEX_V3_SHADOW` | `True` | Shadow + divergence logging ON |
| `PM_LEGACY_OPUS_REVIEW_ENABLED` | `False` | PM Track B through Apex |
| `SENTINEL_LEGACY_PIPELINE_ENABLED` | `False` | Sentinel NEWS_INTERRUPT through Apex |
| `TRADE_ADVISOR_ENABLED` | `False` | Deterministic sizing only |
| `FINBERT_MATERIALITY_GATE_ENABLED` | `True` | FinBERT materiality gate active |

Source: `config.py` lines 163–170, `safety_overlay` dict.

---

## 2. First APEX_SHADOW SCAN_CYCLE Events

| # | Timestamp (UTC) | Fallback | Parse Error | Entries | PM Actions |
|---|----------------|----------|-------------|---------|------------|
| scan-1 | 2026-04-23T22:52:06 | No | No | 0 (vs legacy 6) | 0 (vs legacy 7) |
| scan-2 | 2026-04-23T23:18:07 | **Yes** | **Yes** (token truncation) | 0 | 0 |
| scan-3 | 2026-04-23T23:43:06 | **Yes** | **Yes** (token truncation) | 0 | 0 |

All three were during CLOSED market hours (US market closed at 20:00 UTC).

Shadow log entries: **3**  
Divergence log entries: **2** (scan-2 divergence write failed — see Issue A below)

---

## 3. First APEX_LIVE Runtime Event

**Not yet observed.** Market was CLOSED for all events above. The bot started at 08:50 local (+04) / 04:50 UTC. Market opens at 9:30 AM ET (13:30 UTC / 17:30 +04). First APEX_LIVE SCAN_CYCLE and live Apex execution is expected at that time.

---

## 4. Sentinel Path (NEWS_INTERRUPT)

**Not yet triggered.** Sentinel is active and polling (confirmed in log at 08:51:06 +04): `📡 News Sentinel started | poll every 45s`. No NEWS_INTERRUPT events have fired in the current session. `SENTINEL_LEGACY_PIPELINE_ENABLED: False` means any sentinel trigger will route through Apex.

---

## 5. Paper Order Submission Through Decifer 3.0

**Not yet observed.** No Apex-authoritative orders have been submitted — all events so far are shadow-mode (log-only, no orders). First paper order through the Apex path will occur at market open.

---

## 6. Runtime Issues Identified

### Issue A — `pm_actions` Uninitialized Variable (MEDIUM)

**Symptom:** One divergence write failed with:  
`apex_divergence SCAN_CYCLE write failed (non-fatal): cannot access local variable 'pm_actions' where it is not associated with a value`  
Logged at `2026-04-24 03:18:07 +04`. Caught by the outer try/except — no order impact.

**Root cause:** `pm_actions` is only assigned inside `if should_review and pm_open_pos:` (bot_trading.py line 1599). When `should_review=False` or `pm_open_pos=[]`, the variable is never initialized, but the divergence record at line 2788 references it with `list(pm_actions or [])`. Python raises `UnboundLocalError` on the name lookup before the `or []` short-circuit can help.

**Scope:** Non-fatal in all modes — caught and logged. Divergence records are missing for those cycles but no execution path is affected.

**Proposed fix (one line):** Add `pm_actions = []` immediately before the `if should_review and pm_open_pos:` block (bot_trading.py ~line 1598). Smallest safe change — no logic change, just guarantees the variable exists for the divergence block.

**Status: Awaiting Amit approval before touching code.**

---

### Issue B — Apex JSON Truncation at 2048 Tokens (HIGH — fix before market open)

**Symptom:** 2 of 3 Apex shadow calls returned `parse_error` with the model hitting exactly 2048 output tokens:
- scan-2: `output_tokens: 2047`, error: `Expecting ',' delimiter: line 136 column 6 (char 7243)`
- scan-3: `output_tokens: 2048`, error: `Expecting ',' delimiter: line 156 column 6 (char 6743)`

The model ran out of tokens mid-JSON and the output was truncated. The fallback path correctly set `"fallback": true` and returned empty decisions.

**Root cause:** `llm_client.py` line 33 sets `max_tokens: int = 2048` as the default for `call_apex_with_meta()`. The Apex decision JSON for a 15-position portfolio with 10+ candidates is large enough to exceed this regularly.

**Live-mode consequence:** In live mode (APEX_LIVE), a token-truncated response means Apex returns 0 new entries and 0 portfolio actions — no trades executed that cycle. This is silently conservative (better than a wrong trade), but represents a systematic failure to generate alpha whenever the prompt is large.

**Proposed fix:** In `llm_client.py`, increase the `call_apex_with_meta` default from 2048 to 4096 tokens. The `claude_max_tokens_alpha` config key is already 4096 for the 4-agent pipeline — parity is correct. The Apex synthesizer prompt can be long with many candidates; 2048 was likely set conservatively during early development.

**Status: Awaiting Amit approval before touching code.**

---

## 7. Divergence Pattern (Pre-Market Observation)

In scan-1 (the only clean non-fallback run), divergence was large:

- **ENTRY_MISS_APEX**: 6 events — legacy wanted AMZN, NKE, AON, ABT, PNC, NVDA entries; Apex recommended 0 entries
- **PM_EXIT_CONFLICT (HIGH)**: 7 events — legacy wanted to EXIT AMD, FCX, GE, HON, IBIT, REGN, TGT; Apex recommended HOLD for all

This is expected behavior for the first shadow cycles. The CLOSED-session market context (no intraday data, no DAR) means Apex is correctly being conservative. These divergence patterns are valuable baseline data for the IC tracking exercise. No action required.

---

## 8. Other Warnings Worth Watching

1. **Advisor Opus JSON parse errors (pre-existing):** Multiple `Expecting value: line 1 column 67` errors in `bot_trading.py` advisor calls, triggering formula fallback. These pre-date the cutover and are unrelated to Apex. They are present in `data/audit_log.jsonl` and `signals_log.jsonl`. Worth a dedicated investigation but not a blocker.

2. **FillWatcher extended-hours fallback:** Several extended-hours limit-order substitutions logged (NKE, AON, ABT, PNC, AME, EG, SJM, NI, HTHT, CRS). These are expected behavior — FillWatcher correctly downgrades market orders to limit orders outside regular hours.

3. **DB empty → positions.json fallback (twice):** `2026-04-24 00:14:43` and `02:37:43`. The positions DB was empty on startup and fell back to `positions.json`. Likely from a prior restart cycle. Monitor — if this persists into live session it could cause order duplication or missed positions.

4. **IBKR Warning 2109 on trailing stop orders:** Per existing memory entry, these are benign IBKR warnings (not rejections). Orders transition to PreSubmitted within 400ms. Not a bug.

---

## 9. What to Watch at Market Open

When the US market opens (9:30 AM ET / 17:30 +04):

1. **First APEX_LIVE SCAN_CYCLE log line** — confirms the live path activated
2. **Apex decision quality** — with Issue B potentially fixed, does Apex emit entries and portfolio actions?
3. **First paper order submission** — confirm order goes to IBKR paper account DUP481326
4. **Shadow divergence pattern during live session** — should be smaller than CLOSED session since DAR and intraday data are available
5. **Whether the fallback rate drops** — after token fix, fallback should be ~0%

---

## 10. Legacy Code Status

**Legacy code is intentionally preserved.** The `USE_LEGACY_PIPELINE: False` flag means legacy is not authoritative, but all legacy code paths remain in place for rollback. Rolling back to legacy requires setting `USE_LEGACY_PIPELINE: True` in `config.py` (or `data/settings_override.json`). No legacy code has been deleted. Rollback capability is fully intact.

---

*This document will be updated after the first live APEX_LIVE market-open events are observed.*
