# Activation Handoff Runtime Consumption Proof
**Branch:** `audit/activation-handoff-runtime-consumption-proof`  
**Date:** 2026-05-09  
**Auditor:** Cowork (Claude)  
**Scope:** Read-only runtime consumption audit + one logging-only patch

---

## Verdict

### ⛔ NOT PROVEN — BLOCKED

**Publication of 75 candidates is confirmed.** Runtime consumption by the live bot is **not proven** and is currently **structurally impossible** due to the publisher's Key 2 not being set.

The config flag (Key 1) was set by Sprint 7J.4. The publisher manifest (Key 2) is still in `validation_only` mode with `handoff_enabled=false`. The reader correctly blocks consumption on `handoff_enabled=false`. No bot scan cycle has run since Sprint 7J.4 went live. No handoff source labels appear in any downstream log.

---

## Publication vs Consumption — Full Separation

| Stage | Status | Evidence |
|-------|--------|----------|
| **Published successfully** | ✓ CONFIRMED | `handoff_publisher_report.json`: 75 candidates accepted, 0 rejected, `validation_summary.overall_status=pass` |
| **Manifest valid at publication** | ✓ CONFIRMED | SLA met (5.8s age), `validation_status=pass`, all safety flags false |
| **Key 1 set (config flag)** | ✓ CONFIRMED | `config.py:985`: `enable_active_opportunity_universe_handoff: True` |
| **Key 2 set (manifest enabled)** | ✗ NOT SET | `current_manifest.json`: `handoff_enabled=false`, `publication_mode=validation_only` |
| **Reader called** | CONDITIONAL | Called only when Key 1 is True AND a scan cycle runs; call confirmed by test execution but no live bot scan cycle since flag flip |
| **Candidates admitted** | ✗ NOT PROVEN | Reader returns `handoff_allowed=False` with `fail_closed_reason=handoff_disabled_in_manifest` |
| **Candidates scored** | ✗ NOT PROVEN | 0 handoff candidates in tier_d_funnel.jsonl (1461 total lines) |
| **Candidates survived funnel** | ✗ NOT PROVEN | 0 handoff source labels in signals_log or tier_d_funnel |
| **Candidates dispatched or rejected** | ✗ NOT PROVEN | No `[handoff_wiring] candidate_source=handoff_reader` in any log |

---

## Section A: Runtime Path — Full Trace

### A1. Publication path (confirmed working)

```
handoff_publisher.py --mode <mode>
  └── run_publisher(mode)
       ├── _build_active_universe(publication_mode=mode)       → data/live/active_opportunity_universe.json
       ├── _build_manifest(publication_mode=mode,
       │                   handoff_enabled=(mode=="controlled_activation"))
       │                                                        → data/live/current_manifest.json
       └── _write_run_log_entry()                              → data/live/publisher_run_log.jsonl
```

**Two-key gate design** (Sprint 7J.1):
- Default invocation (`python3 handoff_publisher.py`) → `validation_only` → `handoff_enabled=false`
- Activation invocation (`python3 handoff_publisher.py --mode controlled_activation`) → `handoff_enabled=true`

The publisher does **not** auto-detect the config flag. Both keys must be set independently.

### A2. Bot consumption path (traced from source, not yet executed)

```
bot_trading.py: run_scan_cycle()                              [~line 1546]
  ├── CONFIG.get("enable_active_opportunity_universe_handoff")  → True (Sprint 7J.4)
  ├── _get_handoff_symbol_universe()                           [~line 1558]
  │    ├── handoff_reader.load_production_handoff(
  │    │     "data/live/current_manifest.json")               [handoff_reader.py]
  │    │    ├── read_manifest(path)                           → parse JSON
  │    │    ├── CHECK: manifest["handoff_enabled"]            → False → FAIL CLOSED HERE
  │    │    └── returns handoff_allowed=False,
  │    │             fail_closed_reason="handoff_disabled_in_manifest"
  │    ├── _log_handoff_fail_closed(reason)                   → WARNING in bot log
  │    └── returns ([], {}, "handoff_disabled_in_manifest")
  ├── universe = []                                            [line 1562]
  ├── [favourites and held positions merged into universe]    [lines 1598-1617]
  ├── signal_pipeline.run()                                   [line 1640-ish]
  ├── attach_governance_metadata()                            SKIPPED (fail_closed_reason set)
  ├── [Track B PM review runs — unaffected]
  └── Track A fail-closed guard [~line 2550]
       └── _handoff_fail_closed_reason is not None → Track A SKIPPED, return

IF Key 2 were set (manifest has handoff_enabled=true):
  ├── validate_manifest()                                     → pass
  ├── read_active_universe()                                  → 75 candidates from JSON
  ├── validate_active_universe()                              → pass (if not expired)
  ├── validate_candidate() × 75                               → accepted_candidates list
  ├── build_governance_map(accepted_candidates)               → {symbol: candidate_dict}
  │    [handoff_candidate_adapter.build_governance_map()]
  ├── universe = list(governance_map.keys())                  → up to 75 symbols
  ├── signal_pipeline.run(universe)                           → score all 75
  ├── attach_governance_metadata(pipeline.all_scored,
  │    governance_map)                                        → handoff_* fields attached
  └── Track A: guardrails → apex_cap_score → Apex → execute
```

### A3. Governance metadata attachment (when Key 2 set)

```
handoff_candidate_adapter.attach_governance_metadata(scored_dicts, governance_map)
  For each scored dict whose symbol is in governance_map:
    sd["handoff_symbol"]           = sym
    sd["handoff_route"]            = candidate["route"]
    sd["handoff_source_labels"]    = candidate["source_labels"]
    sd["handoff_reason_to_care"]   = candidate["reason_to_care"]
    sd["handoff_approval_status"]  = candidate["approval_status"]
    sd["handoff_quota_group"]      = candidate["quota_group"]
    ... (other handoff_* fields)
    sd["handoff_executable"]       = False   (invariant)
    sd["handoff_order_instruction"] = None   (invariant)
```

These `handoff_*` fields then propagate through signal_pipeline → signal_dispatcher → tier_d_funnel.jsonl. Their presence in tier_d_funnel is the durable downstream proof of consumption.

---

## Section B: Evidence Found

### B1. Confirmed — Publication working

| File | Key field | Value |
|------|-----------|-------|
| `data/live/current_manifest.json` | `publication_mode` | `validation_only` |
| `data/live/current_manifest.json` | `handoff_enabled` | `false` |
| `data/live/current_manifest.json` | `published_at` | `2026-05-09T09:15:31Z` (refreshed ~12:17Z) |
| `data/live/current_manifest.json` | `ready_for_consumption` | `true` |
| `data/live/handoff_publisher_report.json` | `candidate_summary.accepted_count` | `75` |
| `data/live/handoff_publisher_report.json` | `candidate_summary.rejected_count` | `0` |
| `data/live/handoff_publisher_report.json` | `validation_summary.overall_status` | `pass` |
| `data/live/publisher_run_log.jsonl` (last 5) | Distinct modes seen | Both `controlled_activation` and `validation_only` at `09:10:22Z`; final state is `validation_only` |

### B2. Confirmed — Sprint 7J.4 config flag set

```
config.py:985   "enable_active_opportunity_universe_handoff": True,
```

### B3. Confirmed — Reader rejects manifest correctly

When tested via import during `test_handoff_activation_gate.py`:
```
WARNING bot_trading: [handoff_wiring] FAIL_CLOSED
  mode=controlled_handoff
  handoff_enabled=true                ← config Key 1 is True
  manifest_path=data/live/current_manifest.json
  fail_closed_reason=manifest_expired  ← or handoff_disabled_in_manifest
  scanner_fallback_attempted=False
  apex_input_changed=False
  risk_logic_changed=False
  order_logic_changed=False
  live_output_changed=False
```
(Exact `fail_closed_reason` depends on manifest freshness at test time — either `handoff_disabled_in_manifest` when fresh or `manifest_expired` when stale.)

### B4. Confirmed — No handoff candidates in any downstream log

| Data file | Lines | Handoff-related |
|-----------|-------|-----------------|
| `data/tier_d_funnel.jsonl` | 1,461 | **0** |
| `data/signals_log.jsonl` | (active) | 0 handoff refs |
| `data/universe_coverage.jsonl` | 558 | No `candidate_source` field (pre-patch) |

### B5. Confirmed — Observer safety analysis (at observer's last run time)

From `data/live/handoff_publisher_observation_report.json`:
```json
"safety_analysis": {
  "live_bot_consuming_handoff": false,
  "enable_active_opportunity_universe_handoff": false,   ← stale, pre-Sprint 7J.4
  "handoff_enabled": false,
  "production_candidate_source_changed": false,
  "scanner_output_changed": false,
  "apex_input_changed": false,
  "risk_logic_changed": false,
  "order_logic_changed": false,
  "live_output_changed": false,
  "all_safety_invariants_hold": true
}
```

Note: `enable_active_opportunity_universe_handoff` in the observer report reads `false` — the observer last ran before Sprint 7J.4 flipped the flag. The observer report is a snapshot, not live.

---

## Section C: Missing Evidence

| Evidence needed | Why missing | How to obtain |
|----------------|-------------|---------------|
| `handoff_enabled=true` in manifest | Publisher not invoked with `--mode controlled_activation` | Run: `python3 handoff_publisher.py --mode controlled_activation` |
| `handoff_allowed=True` from reader | Blocked by `handoff_disabled_in_manifest` | Requires controlled_activation manifest |
| `[handoff_wiring] candidate_source=handoff_reader universe=N` in bot log | No bot scan cycle since Sprint 7J.4 + manifest in validation_only | Requires: controlled_activation manifest + live bot scan cycle |
| `handoff_source_labels` in tier_d_funnel.jsonl | 0 handoff candidates have entered the funnel | Requires: full chain above + candidates surviving signal pipeline |
| `candidate_source=handoff` in universe_coverage.jsonl | Field did not exist (pre-patch) | **Fixed by this branch's observability patch** |

---

## Section D: Observability Gap and Patch

### D1. Gap identified

`universe_coverage.jsonl` records every scan cycle's universe composition but does **not** record whether the universe came from the handoff reader or the scanner. Even after a successful controlled_activation run, the only durable proof of consumption would be:
1. Ephemeral bot logs (`logs/decifer.log`) — rotated, not persisted
2. `tier_d_funnel.jsonl` entries with `handoff_source_labels` — proves candidates reached scoring, not just that the reader was called

Without a persistent `candidate_source` field in `universe_coverage.jsonl`, there is no durable record that the handoff path was taken on a given cycle.

### D2. Patch applied (bot_trading.py ~line 1703)

Added two fields to `universe_coverage.jsonl` records:

```python
_handoff_active = (
    CONFIG.get("enable_active_opportunity_universe_handoff", False)
    and not _handoff_fail_closed_reason
)
_cov_record = {
    "ts": ...,
    "regime": ...,
    "candidate_source": "handoff" if _handoff_active else "scanner",   # NEW
    "handoff_fail_closed_reason": _handoff_fail_closed_reason,          # NEW
    ...
}
```

**Properties:**
- `candidate_source`: `"handoff"` only when flag is True AND no fail-closed condition — correctly distinguishes the two paths
- `handoff_fail_closed_reason`: `None` on success, specific reason string on failure — records WHY the handoff was rejected
- Wrapped in existing `try/except` — rotation failure cannot affect trading
- Zero effect on score, signals, candidates, or Apex input
- Backwards compatible — existing fields unchanged

### D3. How this enables proof

When the publisher is run with `--mode controlled_activation` and a bot scan cycle completes:

```json
{
  "ts": "2026-05-XX...",
  "regime": "TRENDING_UP",
  "candidate_source": "handoff",
  "handoff_fail_closed_reason": null,
  "universe": 75,
  "scored": 68,
  "signals": 41
}
```

`candidate_source=handoff` + `handoff_fail_closed_reason=null` + `universe=N` (matching handoff candidate count) is **durable proof** of runtime consumption.

---

## Section E: Validation Results

| Test suite | Result |
|------------|--------|
| Smoke (`-m smoke`) | **9 passed, 1 skipped** — unchanged |
| Activation gate (`test_handoff_activation_gate.py`) | **1 FAILED** — `test_bot_flag_remains_false` (expected — see below) |
| Wiring integration (`test_handoff_wiring_integration.py`) | **116 passed, 2 skipped** — unchanged |
| Intelligence validator | **8/8 PASS** — all intelligence files valid |
| `import bot_trading` | **OK** |
| `import scanner` | **OK** |

### E1. Failing test — expected stale guard

**`tests/test_handoff_activation_gate.py::test_bot_flag_remains_false`**

This test was written in Sprint 7J.1 to prevent premature activation of the bot config flag. Its assertion is:
```python
assert config.CONFIG.get("enable_active_opportunity_universe_handoff") is False
```

Sprint 7J.4 intentionally flipped this flag to `True`. The test is now a stale sprint-phase guard — it served its purpose and must be updated to reflect the new state. It does **not** indicate a regression.

**Required fix (not in this audit branch):** Update `test_bot_flag_remains_false` to assert `True` and rename/reclassify it from a "remains False" guard to a "active in production" assertion. Also update `test_live_bot_not_consuming_handoff` once actual consumption is proven.

---

## Section F: Stale Test Inventory

| Test | Original sprint | Original assertion | Current state | Action needed |
|------|-----------------|--------------------|---------------|---------------|
| `test_bot_flag_remains_false` | Sprint 7J.1 | Flag must stay False | Flag is now True | Update assertion to `is True` |
| `test_live_bot_not_consuming_handoff` | Sprint 7J.1 | Observer safety_analysis shows flag=False | Observer hasn't re-run since 7J.4 | Update once consumption is proven |

---

## Section G: Step 2 Retirement — Decision

### ⛔ Step 2 retirement is NOT yet allowed

The following modules must remain protected until runtime consumption is proven:

| Module | Why protected |
|--------|---------------|
| `handoff_reader.py` | Core reader contract — the production path traced in this audit |
| `handoff_candidate_adapter.py` | Governance attachment adapter — downstream proof point |
| `handoff_publisher.py` | Publisher — must be run in controlled_activation mode to unlock consumption |
| `handoff_publisher_observer.py` | Observer — provides safety analysis that tests depend on |
| `paper_handoff_builder.py` | Shadow validation builder — provides paper_handoff_validation_report.json |
| `paper_handoff_comparator.py` | Shadow comparator — provides paper_handoff_comparison_report.json |
| `_get_handoff_symbol_universe()` in `bot_trading.py` | The bot-side reader call and fail-closed logic |
| `_handoff_fail_closed_reason` guard in `bot_trading.py` | Track A skip guard when handoff fails |

---

## Section H: What Must Happen for PROVEN Status

### H1. Required steps (in order)

**Step 1 — Publisher invocation (Key 2):**
```bash
python3 handoff_publisher.py --mode controlled_activation
```
This writes `handoff_enabled=true` and `handoff_mode=live` to `data/live/current_manifest.json`.  
The manifest is valid for 15 minutes (`expires_at = now + 900s`).

**Step 2 — Bot scan cycle:**
Bot must complete at least one scan cycle within the 15-minute SLA window.  
The cycle must invoke `_get_handoff_symbol_universe()` and succeed.

**Step 3 — Verify persistent proof:**
```bash
tail -1 data/universe_coverage.jsonl | python3 -m json.tool
```
Must show:
```json
{
  "candidate_source": "handoff",
  "handoff_fail_closed_reason": null,
  "universe": <N matching handoff candidate count>
}
```

**Step 4 — Verify downstream proof:**
```bash
python3 -c "
import json
lines = [json.loads(l) for l in open('data/tier_d_funnel.jsonl')]
handoff = [l for l in lines if l.get('stage')=='pipeline' and 'handoff_source_labels' in str(l)]
print(f'handoff candidates in funnel: {len(handoff)}')
"
```
Must return `> 0`.

**Step 5 — Update observer safety_analysis:**
Re-run `handoff_publisher_observer.py` so the report reflects `enable_active_opportunity_universe_handoff=True`.

### H2. Definition of PROVEN

Consumption is proven when ALL of the following hold:
- `candidate_source=handoff` + `handoff_fail_closed_reason=null` in `universe_coverage.jsonl` for at least one cycle
- `[handoff_wiring] candidate_source=handoff_reader universe=N symbols` exists in bot logs for that cycle
- At least one entry with `handoff_source_labels` appears in `tier_d_funnel.jsonl` with a timestamp matching the cycle
- Observer safety_analysis updated to reflect `enable_active_opportunity_universe_handoff=True` and `live_bot_consuming_handoff=True`

---

## Section I: Log Files Inspected

| File | Lines / Size | Finding |
|------|-------------|---------|
| `data/live/current_manifest.json` | — | `handoff_enabled=false`, `publication_mode=validation_only`, expires `09:30:31Z` (refreshed ~12:17Z) |
| `data/live/handoff_publisher_report.json` | — | `publication_mode=validation_only`, 75 candidates accepted, `enable_active_opportunity_universe_handoff_config_state=false` (stale) |
| `data/live/publisher_run_log.jsonl` | 146 records | Last 5: one `controlled_activation` run + one `validation_only` run at same timestamp; final state is `validation_only` |
| `data/live/handoff_publisher_observation_report.json` | — | Observer last ran at `09:15:36Z`; `live_bot_consuming_handoff=false`; all safety invariants hold; `enable_active_opportunity_universe_handoff=false` (stale — pre-Sprint 7J.4) |
| `data/tier_d_funnel.jsonl` | 1,461 lines | 0 handoff-related entries |
| `data/universe_coverage.jsonl` | 558 records | Last entry: `2026-05-08T21:47Z` (pre-Sprint 7J.4); no `candidate_source` field (pre-patch) |
| `data/signals_log.jsonl` | (active) | 0 handoff references |
| `logs/` | Empty | No bot log files present in worktree |

---

## Section J: Candidate Counts Available

From `handoff_publisher_report.json`:
- Shadow universe source count: **75**
- Accepted by publisher: **75**
- Rejected by publisher: **0**
- Structural cap candidates (Tier B, high conviction): **35**
- Remaining (Tier C dynamic): **40**

These candidates are published and valid. None have entered the bot runtime candidate pool because `handoff_disabled_in_manifest` prevents consumption.

---

## Summary

```
CONFIG flag (Key 1):  enable_active_opportunity_universe_handoff = True  ✓ (Sprint 7J.4)
Manifest flag (Key 2): handoff_enabled = false                           ✗ (still validation_only)
Reader path:          Wired correctly, calls load_production_handoff()   ✓ (code traced)
Reader result:        handoff_allowed=False, reason=handoff_disabled_in_manifest ✓ (correct fail-closed)
Bot scan cycle:       No scan cycle since Sprint 7J.4 went live          ✗
Downstream evidence:  0 handoff entries in tier_d_funnel / signals_log   ✗
Observer:             Stale — reads flag state from before Sprint 7J.4   ⚠️
Stale tests:          test_bot_flag_remains_false FAILS (expected)        ⚠️

VERDICT: NOT PROVEN — BLOCKED on publisher Key 2
ACTION:  python3 handoff_publisher.py --mode controlled_activation
         then verify bot scan cycle produces candidate_source=handoff in universe_coverage.jsonl
```
