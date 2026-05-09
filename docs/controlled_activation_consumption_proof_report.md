# Controlled Activation Consumption Proof Report
**Branch:** `activation/prove-controlled-handoff-consumption`  
**Date:** 2026-05-09  
**Auditor:** Cowork (Claude)

---

## Final Status

### ✅ PROVEN — Reader and Adapter Path

The live bot runtime (`bot_trading._get_handoff_symbol_universe()`) demonstrably consumes the active opportunity universe handoff when both activation keys are set. The production functions were called with a valid controlled-activation manifest and returned 70 governance-tagged symbols with `fail_closed_reason=None`.

**Downstream signal scoring** (tier_d_funnel, signals_log) requires market hours to complete. The market is pre-open at proof time (12:30 UTC / 8:30 AM EDT). The bot process is running (PID 72626) and will produce `handoff_source_labels` in tier_d_funnel on its first scan cycle when the market opens, provided the manifest is re-published in `controlled_activation` mode.

---

## Commands Run

### Task A — Test fix
```python
# tests/test_handoff_activation_gate.py: tests 18 and 19 replaced
# tests/test_handoff_publisher.py: 2 stale tests updated
```

### Task B — Controlled activation manifest
```bash
cd "decifer trading/.claude/worktrees/controlled-activation-proof"
python3 handoff_publisher.py --mode controlled_activation
```
Output: `[handoff_publisher] SUCCESS: 75 candidates published. publication_mode=controlled_activation handoff_enabled=true live_output_changed=false`

### Task C — Proof runner (actual production functions)
```bash
python3 - <<'PROOF_EOF'
# Called: handoff_reader.load_production_handoff()
# Called: bot_trading._get_handoff_symbol_universe()
# Called: handoff_candidate_adapter.build_governance_map()
# Called: handoff_candidate_adapter.attach_governance_metadata()
# Wrote:  data/universe_coverage.jsonl
PROOF_EOF
```

---

## Manifest State — Before Activation

| Field | Value |
|-------|-------|
| `publication_mode` | `validation_only` |
| `handoff_enabled` | `false` |
| `published_at` | `2026-05-09T12:27:03Z` |
| `expires_at` | `2026-05-09T12:42:03Z` |
| Reader result | `handoff_allowed=False`, `fail_closed_reason=handoff_disabled_in_manifest` |

---

## Manifest State — After Activation (Key 2 Set)

| Field | Value |
|-------|-------|
| `publication_mode` | `controlled_activation` |
| `handoff_enabled` | `true` |
| `handoff_mode` | `live` |
| `published_at` | `2026-05-09T12:27:15Z` |
| `expires_at` | `2026-05-09T12:42:15Z` |
| `validation_status` | `pass` |
| `ready_for_consumption` | `true` |
| `age_seconds` | `9.3` |
| `ttl_remaining_s` | `890.7` |
| SLA primary threshold (600s) | **MET** |
| Reader result | `handoff_allowed=True`, `fail_closed_reason=None` |
| `accepted_candidate_count` | `70` |
| `rejected_candidate_count` | `5` |

---

## Proof Runner Results

### Step 1 — Reader verification
```
handoff_allowed:          True
fail_closed_reason:       None
accepted_candidate_count: 70
rejected_candidate_count: 5
```

### Step 2 — `bot_trading._get_handoff_symbol_universe()`
```
symbols returned:       70
governance_map symbols: 70
fail_closed_reason:     None
first 5 symbols:        ['ASTS', 'GLD', 'IBIT', 'USO', 'SPY']
```

### Step 3 — Governance metadata attachment
```
ASTS: route='manual_conviction'  source_labels=['favourites_manual_conviction', 'legacy_theme_tracker_read_only', 'committed_universe_read_only']  executable=False  order_instruction=None
GLD:  route='manual_conviction'  source_labels=['favourites_manual_conviction', 'committed_universe_read_only']  executable=False  order_instruction=None
IBIT: route='manual_conviction'  source_labels=['favourites_manual_conviction', 'legacy_theme_tracker_read_only', 'committed_universe_read_only']  executable=False  order_instruction=None
```

All `handoff_executable=False` ✓  
All `handoff_order_instruction=None` ✓  
All `handoff_source_labels` populated ✓

---

## `data/universe_coverage.jsonl` — Proof Record

```
tail -1 data/universe_coverage.jsonl | python3 -m json.tool
```

```json
{
    "ts": "2026-05-09T12:30:57.186997+00:00",
    "regime": "PROOF_RUN_PRE_MARKET",
    "candidate_source": "handoff",
    "handoff_fail_closed_reason": null,
    "core": -1,
    "equities": -1,
    "promoted": -1,
    "other": -1,
    "favs": -1,
    "held": -1,
    "opts": -1,
    "universe": 70,
    "scored": 0,
    "signals": 0,
    "proof_run": true,
    "manifest_publication_mode": "controlled_activation",
    "manifest_handoff_enabled": true,
    "manifest_age_s": 222.2,
    "manifest_ttl_s": 677.8,
    "manifest_sla_met": true,
    "accepted_candidate_count": 70,
    "rejected_candidate_count": 5,
    "governance_map_symbols": 70,
    "scanner_fallback_attempted": false,
    "apex_input_changed": false,
    "live_output_changed": false
}
```

**`candidate_source = "handoff"` ✓**  
**`handoff_fail_closed_reason = null` ✓**  
**`universe = 70` (handoff candidate count) ✓**  
**`manifest_sla_met = true` ✓**  
**`scanner_fallback_attempted = false` ✓**

---

## Downstream Logs — Task E

| Log | Entries | Handoff entries | Notes |
|-----|---------|-----------------|-------|
| `data/tier_d_funnel.jsonl` | 1,461 | **0** | Market not open — no signal scoring pass since proof run |
| `data/signals_log.jsonl` | active | 0 | Same reason — no Apex call without market data |
| `data/universe_coverage.jsonl` | 559 | **1** (proof record) | ✓ proof record written |

**Tier D funnel explanation:** Handoff `source_labels` appear in `tier_d_funnel.jsonl` only after `attach_governance_metadata()` runs on scored dicts that reach the dispatcher. This requires:
1. `signal_pipeline.run()` to score the handoff symbols (needs Alpaca price data → market hours)
2. At least one symbol to reach `min_score_to_score` and enter the funnel write path

The proof runner did not include a scoring pass (scores=0, signals=0) because running Alpaca data fetches in a pre-market proof context would add network dependencies to the proof. The reader and adapter calls are the load-bearing path.

**Expected at market open:** When the bot's next scan cycle completes with a `controlled_activation` manifest, `tier_d_funnel.jsonl` will contain entries with `handoff_source_labels`, and `universe_coverage.jsonl` will contain a non-proof record with `candidate_source=handoff`, `scored > 0`, `signals > 0`.

---

## Two-Key Activation Status

| Key | Description | State |
|-----|-------------|-------|
| **Key 1** | `config.py: enable_active_opportunity_universe_handoff = True` | ✓ ACTIVE (Sprint 7J.4) |
| **Key 2** | Publisher invoked with `--mode controlled_activation` → `handoff_enabled=true` | ✓ ACTIVATED (this branch), restored to `validation_only` post-proof |

For market-open consumption, Key 2 must be re-activated before the first scan cycle:
```bash
python3 handoff_publisher.py --mode controlled_activation
```

The publisher runs periodically and writes fresh manifests. If the cron/scheduler runs it in `controlled_activation` mode, the bot will consume the handoff on every scan cycle until explicitly reverted.

---

## Tests Updated

### `tests/test_handoff_activation_gate.py` — Tests 18 and 19 replaced

| Old test | Problem | Replacement |
|----------|---------|-------------|
| `test_bot_flag_remains_false` | Sprint 7J.1 guard; asserted flag=False; broken by Sprint 7J.4 | `test_two_key_gate_key1_now_active` — asserts flag=True AND proves Key 2 is still required independently |
| `test_live_bot_not_consuming_handoff` | Read stale observer report; asserted observer flag=False; observer pre-dates Sprint 7J.4 | `test_track_a_blocked_without_key2` — proves validation_only manifest causes Track A skip regardless of Key 1 |

### `tests/test_handoff_publisher.py` — 2 stale sprint-phase guards updated

| Old test | Problem | Replacement |
|----------|---------|-------------|
| `TestSafetyInvariants::test_enable_active_opportunity_universe_handoff_config_false` | Asserted config flag=False; stale | `test_enable_active_opportunity_universe_handoff_config_true_sprint7j4` — asserts flag=True, guards against rollback |
| `TestSprintRegression::test_enable_handoff_flag_still_false` | Same issue | `test_enable_handoff_flag_active_sprint7j4` — asserts flag=True, guards against rollback |

---

## Tests Run

| Suite | Result |
|-------|--------|
| Smoke (`-m smoke`) | **9 passed, 1 skipped** |
| `test_handoff_activation_gate.py` | **20 passed** |
| `test_handoff_publisher.py` | **part of 309 passed, 5 skipped** |
| `test_handoff_publisher_observer.py` | **part of 309 passed, 5 skipped** |
| `test_handoff_wiring_integration.py` | **part of 309 passed, 5 skipped** |
| All handoff suites combined | **309 passed, 5 skipped, 0 failed** |
| Intelligence validator | **8/8 PASS** |
| `import bot_trading` | **OK** |
| `import scanner` | **OK** |

---

## Proof Confidence Assessment

| Claim | Evidence | Confidence |
|-------|---------|------------|
| Config Key 1 is active | `config.py:985: True`, test asserts | **CERTAIN** |
| Publisher Key 2 works | 75 candidates, `handoff_enabled=true` manifest | **CERTAIN** |
| Reader accepts controlled_activation manifest | `handoff_allowed=True`, `accepted_candidate_count=70` | **CERTAIN** |
| `_get_handoff_symbol_universe()` returns symbols | 70 symbols, `fail_closed_reason=None` | **CERTAIN** |
| Governance metadata attaches correctly | `handoff_route`, `handoff_source_labels` populated; `executable=False`; `order_instruction=None` | **CERTAIN** |
| `candidate_source=handoff` in universe_coverage | Record confirmed in file | **CERTAIN** |
| Handoff symbols enter signal pipeline | Requires market hours | **PENDING** |
| Handoff source labels in tier_d_funnel | Requires market hours + scoring pass | **PENDING** |
| No trading behaviour changed | Zero score/risk/order logic touched | **CERTAIN** |

---

## Step 2 Retirement — Decision

### ⚠️ PARTIALLY UNBLOCKED — Reader and adapter paths proven; downstream scoring pending

| Condition | Status |
|-----------|--------|
| Reader path proven | ✓ |
| Governance adapter path proven | ✓ |
| Two-key activation model intact | ✓ |
| `candidate_source=handoff` in universe_coverage | ✓ (proof record) |
| Handoff candidates in tier_d_funnel | ✗ pending market open |
| Full scan cycle with `scored > 0` | ✗ pending market open |

**Recommendation:** Step 2 retirement (shadow module cleanup) can begin as soon as the bot completes one full market-hours scan cycle with `controlled_activation` manifest active, producing:
- A non-proof `universe_coverage.jsonl` record with `candidate_source=handoff`, `scored > 0`
- At least one `tier_d_funnel.jsonl` entry from that cycle with `handoff_source_labels` populated

Until that occurs, all handoff, advisory, intelligence, shadow, comparator, and quota modules remain protected.

---

## Action Required for Full Market Proof

At or before market open (13:30 UTC / 9:30 AM EDT):

```bash
# Re-activate Key 2 before market open
python3 handoff_publisher.py --mode controlled_activation

# After first scan cycle completes, verify:
tail -1 data/universe_coverage.jsonl | python3 -m json.tool
# Must show: candidate_source=handoff, scored > 0, proof_run absent

# Verify downstream:
python3 -c "
import json
lines = [json.loads(l) for l in open('data/tier_d_funnel.jsonl')]
handoff = [l for l in lines if l.get('stage') == 'pipeline' and 'handoff_source_labels' in str(l)]
print(f'Handoff candidates in funnel: {len(handoff)}')
"
```
