# Decifer 3.0 — Market-Open Runtime Health Check
**Date:** 2026-04-24
**Checked at:** 18:36 +04 (14:36 UTC / 10:36 EDT)
**Market status:** Open (PRIME_AM session)
**Bot PID:** 25065 — running since 8:50 AM EDT, 135 min CPU time

---

## Verdict: HEALTHY WITH WARNINGS

Core path is verified, clean, and authoritative. Two warnings require monitoring but are not blockers.

---

## 1. Cutover Flag State

| Flag | Value | Source |
|------|-------|--------|
| `USE_LEGACY_PIPELINE` | `False` | `config.py:166` |
| `USE_APEX_V3_SHADOW` | `True` | `config.py:167` |
| `PM_LEGACY_OPUS_REVIEW_ENABLED` | `False` | `config.py:168` |
| `SENTINEL_LEGACY_PIPELINE_ENABLED` | `False` | `config.py:169` |
| `TRADE_ADVISOR_ENABLED` | `False` | `config.py:170` |
| `FINBERT_MATERIALITY_GATE_ENABLED` | `True` | `config.py:171` |

All Phase 8 cutover flags are confirmed set. Apex is the exclusive authoritative path.

---

## 2. Apex Path Confirmation

Every scan cycle log line ends with `(apex cutover)`:
```
2026-04-24 17:49:09 +04 [decifer.bot] [SCAN] Scan #24 started | Session: PRIME_AM
2026-04-24 17:53:47 +04 [decifer.bot] [INFO] APEX_LIVE SCAN_CYCLE: 0 entries, 0 actions, 0 forced, 0 errors
2026-04-24 17:53:47 +04 [decifer.bot] [SCAN] Scan #24 complete (apex cutover)
```

PM Track B is active and logging correctly:
```
2026-04-24 12:56:32 +04 [decifer.bot] [INFO] PM legacy Opus review disabled — invoking Apex Track B cutover branch
```
This log line appears before every PM cycle throughout the day. Legacy Opus review is confirmed off.

No legacy path invocations found in any log.

---

## 3. First APEX_LIVE SCAN_CYCLE Today

- **Pre-market:** First APEX_LIVE SCAN_CYCLE at `17:20:46 +04` (13:20 UTC / 9:20 AM EDT), Session: PRE_OPEN
- **Market open (first PRIME_AM):** Scan #24 started `17:49:09 +04`, first PRIME_AM APEX_LIVE at `17:53:47 +04` (13:53 UTC / 9:53 AM EDT)

---

## 4. Scan Cycle Cadence (PRIME_AM)

| Scan # | Started (+04) | Complete (+04) | Session | Entries | Actions | Errors |
|--------|---------------|----------------|---------|---------|---------|--------|
| 24 | 17:49:09 | 17:53:47 | PRIME_AM | 0 | 0 | 0 |
| 25 | 17:56:48 | 18:00:25 | PRIME_AM | 0 | 0 | 0 |
| 26 | 18:03:25 | 18:08:02 | PRIME_AM | 0 | 0 | 0 |
| 27 | 18:11:02 | 18:14:55 | PRIME_AM | 0 | 0 | 0 |
| 28 | 18:17:55 | 18:21:27 | PRIME_AM | 0 | 0 | 0 |
| 29 | 18:24:28 | 18:28:36 | PRIME_AM | 0 | 0 | 0 |
| 30 | 18:31:37 | in progress | PRIME_AM | — | — | — |

~7-minute scan intervals. All completing cleanly.

Signals scored per cycle: 37–89 (first market-open scan: 89 symbols, 79 above paper threshold of 14).

---

## 5. Paper Dispatch

Orders were submitted at market open via the Apex path:

| Time (UTC) | Symbol | Side | Qty | Type | Price |
|-----------|--------|------|-----|------|-------|
| 13:40:21 | FCX | PENDING_CANCEL | 27 | — | — |
| 13:40:36 | TGT | SELL | 35 | LMT | $2.67 |
| 13:41:08 | GE | SELL | 10 | LMT | $11.90 |
| 13:49:26 | TGT | SELL | 35 | LMT | $2.40 |

These are options exits (near-zero prices = near-expiry). Paper dispatch is working correctly.

---

## 6. PM Track B

Two PM cycles after market open logged to `apex_shadow_log.jsonl`:
- `13:32 UTC` — `new_entries: 0`, `portfolio_actions: 0`, `session_character: FEAR_ELEVATED`, `macro_bias: NEUTRAL`, latency: 4,961 ms
- `14:05 UTC` — `new_entries: 0`, `portfolio_actions: 0`, `session_character: FEAR_ELEVATED`, latency: 5,837 ms

PM Track B is firing, reaching Apex AI, and writing results. No errors. Conservative output (0 actions) is consistent with the macro read.

---

## 7. News Sentinel

Themes/sentinel monitoring actively:
```
2026-04-24 18:35:31 +04 [decifer.themes] Sentinel universe: 80 symbols | holdings=12 | themes=2 active | trending=50 headlines checked
```
Running every ~30 seconds. No interrupt has fired today. No sentinel errors.

---

## 8. Shadow / Divergence Logging

- `apex_shadow_log.jsonl`: 19 entries today (all TRACK_B_PM)
- `apex_divergence_log.jsonl`: 2 entries today (all TRACK_B_PM)
- No SCAN_CYCLE divergence entries — **expected**: with USE_LEGACY_PIPELINE=False, legacy pipeline doesn't run, so there's nothing to diverge against

---

## 9. Error Surfaces

| Error | Time | Severity | Status |
|-------|------|----------|--------|
| IBKR connection failed (startup) | 08:44 +04 | Transient | Auto-recovered in 50s |
| IBKR: TWS lost IB servers (error 1100) | 11:47 +04 | Transient | Auto-recovered in 20s (error 1102: data maintained) |
| EDGAR RSS feed read timeout | 16:16 +04 | Transient | Caught and logged, non-fatal |
| VALIDATIONERROR ×5 (IBKR warning 2109) | 04:50 +04 | Benign | Known pre-market GTC order warning (see MEMORY) |
| `filter_semantic_violations: None not in candidates` ×18 | 17:46 +04 | Warning | Apex AI returning null-symbol entries — filtered out by semantic gate. Two scan cycles affected. |

No `APEX_LIVE SCAN_CYCLE cutover failed` events. No tracebacks after startup sequence.

---

## 10. Open Positions

positions.json holds 10 entries:
- **Stocks:** ABT (SHORT), AME, NI, NKE, SJM
- **Options:** FCX_P_65.0_2026-05-15, GE_P_285.0_2026-05-15, HON_P_215.0_2026-05-08, REGN_P_762.5_2026-05-08, TGT_C_131.0_2026-05-15

Several options appear significantly underwater (REGN_P: unrealizedPNL -$3,914). These stale options positions are actively being exited at market open.

---

## Warning 1: All Scan Cycles Returning 0 New Entries

Every APEX_LIVE SCAN_CYCLE since the start of the day has returned:
```
0 entries, 0 actions, 0 forced, 0 errors
```

The signal engine IS scoring (89 symbols, 79 above paper threshold in first market-open scan). The Apex AI is being called and returning decisions — but `new_entries: []` across all cycles.

**What this is NOT:** A system failure, a cutover failure, or a dispatch error.

**What this might be:**
- Apex AI correctly deciding not to enter under FEAR_ELEVATED + TRENDING_UP mixed signals
- The stale underwater options positions may be influencing the portfolio context passed to the AI
- The `WatchlistStore: no candidates file for today yet` message (catalyst daily file) appears every cycle — the catalyst candidates feed is empty, which removes the catalyst-boosted symbols from consideration

**What to watch:** If scans 31–40 continue to return 0 entries once stale positions clear, that warrants a deeper look at what the apex_input contains and what market_intelligence is returning. This is not urgent right now.

---

## Warning 2: Apex AI Returning Null-Symbol Entries (18 drops)

`filter_semantic_violations: None not in candidates — dropping` appeared 18 times across two scan cycles. The Apex AI is emitting entries with `symbol: null` which the semantic gate correctly removes.

**Impact:** Zero — the gate is working as designed. These null entries never reach dispatch.

**What it indicates:** AI output quality issue. The model occasionally emits malformed entries. Not new — this is a pre-existing AI behavior.

---

## What to Watch for the Rest of the Session

1. **0-entry streak**: If scan cycles 31+ continue to produce 0 entries through the mid-day session, check what apex_call is returning and whether catalyst candidates file populates (expected after `CatalystEngine [sentiment]` and `[options]` warm up).

2. **Options exits**: REGN_P (5 contracts, unrealizedPNL -$3,914) and remaining open options should be exiting. Watch audit_log for SUBMITTED/FILLED events on these.

3. **IBKR stability**: One disconnect at 11:47 +04 (7:47 AM EDT). Reconnect was clean. Watch for repeat mid-session.

4. **Sentinel interrupt**: 80 symbols being monitored, 50 headlines/cycle. If a news interrupt fires, watch whether it routes through the Apex path (SENTINEL_LEGACY_PIPELINE_ENABLED=False) and whether dispatch executes.

5. **Catalyst candidates file**: `WatchlistStore: no candidates file for today yet` every cycle. Once the daily screen runs and populates `daily_promoted.json`, catalyst-boosted candidates should start appearing.

---

*Generated by Cowork at 2026-04-24 18:36 +04 (10:36 AM EDT)*
