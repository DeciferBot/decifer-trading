# Intelligence-First: Controlled Handoff Wiring Design

**Sprint:** 7D  
**Date:** 2026-05-07  
**Status:** Design only — not implemented  
**Blocked on:** Amit approval before Sprint 7E implementation

---

## 1. Purpose

Define how the live bot will switch its candidate source from the scanner-led pipeline to `handoff_reader.py` (the production candidate-source boundary reader), behind a feature flag, with rollback and fail-closed behaviour. This change must not alter risk logic, execution logic, scoring logic, or Apex reasoning. It replaces only the input list of symbols that enter scoring.

This design does **not** change:
- How signals are computed
- How Apex reasons or decides
- How orders are sized, placed, or managed
- How positions are tracked or closed
- Any PM, risk, or guardrails logic

---

## 2. Current State

| Component | Status |
|-----------|--------|
| Scanner-led candidate source | **Active** — `get_dynamic_universe()` in `scanner.py` |
| `handoff_reader.py` | Built and validated (Sprint 7B) |
| `paper_handoff_builder.py` | Built (Sprint 7B) |
| `paper_handoff_comparator.py` | Built (Sprint 7C) |
| `data/live/current_manifest.json` | **Not written** |
| `data/live/active_opportunity_universe.json` | **Not written** |
| `enable_active_opportunity_universe_handoff` | `False` |
| `handoff_enabled` | `False` |
| Production handoff | **Blocked** |

The flag `enable_active_opportunity_universe_handoff` exists in `config.CONFIG` at `config.py:971` but is not referenced in `bot_trading.py` or `scanner.py`. No wiring exists yet.

---

## 3. Current Candidate-Source Flow (read-only reference)

```
bot_trading.run_scan()                    [bot_trading.py:1242]
    │
    ├─ get_market_regime(ib)              [bot_trading.py:1352]
    │
    ├─ get_dynamic_universe(ib, regime)   [bot_trading.py:1447]  ← WIRING POINT
    │     scanner.py:363
    │     Returns: list[str]  (symbol strings, up to ~235 symbols)
    │     Source: Tier A (core ETFs/equities) + Tier B (promoted) +
    │             Tier C (sector rotation) + Tier D (position research)
    │
    ├─ run_signal_pipeline(universe, ...)  [bot_trading.py:1533]
    │     signal_pipeline.py:649
    │     Input: list[str]
    │     Internally: filters, fetches news, calls score_universe()
    │     Returns: SignalPipelineResult(.all_scored, .scored)
    │
    ├─ pipeline.all_scored                 [bot_trading.py:1541]
    │     list[dict] — scored candidate dicts
    │     Each dict: symbol, score, raw_score, price, atr, signal dimensions...
    │
    ├─ guardrails filter                   [bot_trading.py:2420]
    │     _fc_track_a(all_scored, open_positions, regime)
    │
    ├─ apex_cap_score attachment           [bot_trading.py:2429]
    │
    ├─ dedup + cap to 50                   [bot_trading.py:2439-2494]
    │
    └─ build_scan_cycle_apex_input()       [bot_trading.py:2848]
          apex_orchestrator.py:106
          Input: candidates (capped scored dicts), review_positions, portfolio_state...
          Returns: {"track_a": {"candidates": [...]}, "track_b": [...], ...}
```

---

## 4. Target Wiring Point

**Location:** `bot_trading.py:1447` — where `get_dynamic_universe()` is currently called.

**Design:** Add a single conditional branch before the existing `get_dynamic_universe()` call:

```python
if config.CONFIG.get("enable_active_opportunity_universe_handoff"):
    universe = _get_handoff_symbol_universe()   # NEW function — reads handoff, extracts symbols
    if universe is None:
        # Fail closed — do not fall back to scanner
        _log_handoff_fail_closed(reason="handoff_read_failed")
        return  # or skip new entries for this cycle
else:
    universe = get_dynamic_universe(ib, regime)  # existing path — unchanged
```

Where `_get_handoff_symbol_universe()` is a small private function in `bot_trading.py` that:
1. Calls `handoff_reader.load_production_handoff("data/live/current_manifest.json")`
2. Checks `result["handoff_allowed"]` — if False, returns None (fail closed)
3. Extracts the symbol list from accepted candidates
4. Attaches governance metadata to a module-level dict for later use in scoring
5. Returns the symbol list

### Why at `get_dynamic_universe()`, not deeper

- `get_dynamic_universe()` returns `list[str]` — the simplest possible interface
- Wiring at this point leaves signal scoring (`score_universe()`), guardrails, Apex cap, and all downstream logic completely unchanged
- No changes to `signal_pipeline.py`, `signals/__init__.py`, `apex_orchestrator.py`, or any risk/order code
- Governance metadata (route, theme_ids, etc.) is attached to scored dicts after scoring via a thin adapter — not mixed into the scoring computation

### What returns

The handoff symbol universe returns `list[str]` (identical type to `get_dynamic_universe()`). Signal scoring proceeds identically on these symbols.

---

## 5. Governance Metadata Attachment

After `run_signal_pipeline()` returns, a thin adapter enriches scored dicts with governance metadata from the handoff candidates. This happens at `bot_trading.py` after line 1541:

```python
if config.CONFIG.get("enable_active_opportunity_universe_handoff"):
    _attach_handoff_governance(pipeline.all_scored, _handoff_governance_map)
```

Where `_attach_handoff_governance()`:
- Takes each scored dict
- Looks up the symbol in `_handoff_governance_map` (populated by `_get_handoff_symbol_universe()`)
- Attaches fields: `route_hint`, `theme_ids`, `risk_flags`, `confirmation_required`, `approval_status`, `quota_group`, `source_labels`, `reason_to_care`
- Does NOT modify `score`, `raw_score`, or any signal dimension
- Is a pure function — no side effects, no I/O

If a symbol has no governance metadata (e.g. symbol was in handoff but scored dict is missing), the dict is left unchanged.

---

## 6. Candidate Adapter Shape

The handoff candidate shape (from `handoff_reader.py`) maps to the scored dict shape as follows:

| Handoff field | Scored dict destination | Notes |
|--------------|------------------------|-------|
| `symbol` | `symbol` | Direct — already present |
| `route` | `handoff_route` | Added by adapter |
| `route_hint` | `handoff_route_hint` | Added by adapter |
| `reason_to_care` | `handoff_reason_to_care` | Added by adapter |
| `source_labels` | `handoff_source_labels` | Added by adapter |
| `theme_ids` | `handoff_theme_ids` | Added by adapter |
| `risk_flags` | `handoff_risk_flags` | Added by adapter |
| `confirmation_required` | `handoff_confirmation_required` | Added by adapter |
| `approval_status` | `handoff_approval_status` | Added by adapter |
| `quota_group` | `handoff_quota_group` | Added by adapter |
| `executable` | Not attached — always False | Never passed to Apex |
| `order_instruction` | Not attached — always null | Never passed to Apex |

**Prefix `handoff_` on all governance fields** to avoid collisions with existing scored dict fields and to make it immediately clear which data is from the handoff vs the live signal engine.

A minimal module `handoff_candidate_adapter.py` provides one pure function:
```python
def attach_governance_metadata(
    scored_dicts: list[dict],
    governance_map: dict[str, dict],
) -> None:
    """In-place attachment. Pure lookup — no I/O, no side effects."""
```

Classification: **adapter-only**. Does not import scanner, orders, bot_trading, or LLM.

---

## 7. Feature Flag Behaviour

### `enable_active_opportunity_universe_handoff = False` (current state)

- `get_dynamic_universe()` is called exactly as today
- No manifest read
- No active universe read
- `handoff_reader` is not imported or called in the live bot path
- No `live_output_changed`
- Scanner output unchanged
- Signal scoring unchanged
- Apex input unchanged

### `enable_active_opportunity_universe_handoff = True` (future state)

1. Read `data/live/current_manifest.json` via `handoff_reader.read_manifest()`
2. Validate manifest via `handoff_reader.validate_manifest()`
3. If manifest invalid → fail closed (see Section 9)
4. Read active universe file referenced by manifest
5. Validate active universe
6. If universe invalid → fail closed
7. Validate all candidates
8. Extract symbol list from accepted candidates
9. Populate governance map
10. Call `run_signal_pipeline(handoff_symbols, ...)`
11. After scoring, attach governance metadata
12. Continue through existing guardrails, cap, and Apex path unchanged

---

## 8. Optional Dry-Run Compare Mode

**Flag:** `enable_handoff_dry_run_compare` (separate from handoff flag, default `False`)

When `True`:
- Current scanner path remains the **source of truth** for all entries
- `handoff_reader` loads the production manifest in parallel (read-only)
- Compares the two candidate sets
- Writes a comparison log entry to `data/live/handoff_dry_run_compare_log.jsonl`
- Does NOT replace candidate source
- Does NOT change Apex input
- `live_output_changed = false`

This is NOT the same as the paper comparator (which is a one-shot batch tool). The dry-run compare mode is an inline observation hook that runs every scan cycle. It must not add latency to the critical path — implement as a fire-and-forget thread or post-cycle log write.

**Critical distinction:**

| Mode | Flag | Source of truth | Apex input changed |
|------|------|-----------------|--------------------|
| Dry-run compare | `enable_handoff_dry_run_compare=True` | Scanner | No |
| Production handoff | `enable_active_opportunity_universe_handoff=True` | Handoff reader | No (same scoring) |

---

## 9. Fail-Closed Design

On any failure when `enable_active_opportunity_universe_handoff=True`:

| Failure | Behaviour |
|---------|-----------|
| Manifest file missing | Fail closed: skip new entries this cycle. Log reason. Do not fall back to scanner. |
| Manifest invalid JSON | Fail closed: same as missing. |
| Manifest expired | Fail closed: log staleness. |
| `validation_status != "pass"` | Fail closed. |
| `handoff_enabled = False` in manifest | Fail closed. This is the Sprint 7B/7C state. |
| Active universe file missing | Fail closed. |
| Active universe expired | Fail closed. |
| Zero accepted candidates | Fail closed. |
| Handoff reader exception | Fail closed. Log full exception. Do not crash bot. |
| Symbol extraction returns empty list | Fail closed. |
| `handoff_allowed = False` in result | Fail closed. |

**"Fail closed" means:**
- New entries for this scan cycle are skipped (no new orders)
- Existing position management (PM Track B) continues using the existing PM path, which is independent of candidate discovery
- The bot process is NOT killed
- The fail-closed reason is logged to the standard bot log
- The flag state is logged
- `live_output_changed = false`
- No scanner substitution
- No Apex call for new entries

---

## 10. Apex Boundary (Invariants)

When handoff is enabled:

| Rule | Enforcement |
|------|-------------|
| Apex receives only handoff-originated candidates | Enforced by wiring — only handoff symbols enter `score_universe()` |
| Apex may not discover symbols | Apex receives no scanner output; cannot add symbols not in handoff |
| Apex may not create themes | Themes come from handoff governance metadata, not Apex prompt |
| Apex may not override source eligibility | Approval status is set by handoff validation, not by Apex |
| Apex may not re-add scanner-only symbols | Scanner is not called when flag is True |
| Apex may reject/defer handoff candidates | Apex PM Track B can still defer or trim positions |
| Apex receives governance metadata as context | `handoff_route_hint`, `handoff_theme_ids`, `handoff_risk_flags` available in scored dict |
| Apex may not suppress structural candidates solely for 5-minute noise | Existing Apex prompt contract unchanged |

---

## 11. Rollback Design

| Action | Effect |
|--------|--------|
| Set `enable_active_opportunity_universe_handoff = False` | Scanner path restores immediately on next scan cycle |
| Code revert | Not required |
| Restart | Preferred but not required if config reload is supported |
| Handoff files remain after rollback | Yes — files are not deleted. Rollback does not corrupt data. |
| Advisory logging | Independent — may remain on or off |

Config reload path (preferred): if `config.py` supports hot reload via `data/settings_override.json`, the flag flip takes effect within one scan cycle without restart.

---

## 12. Logging and Observability

Required log events when `enable_active_opportunity_universe_handoff=True`:

```
[handoff_wiring] flag_state=True
[handoff_wiring] manifest_read_attempted=True path=data/live/current_manifest.json
[handoff_wiring] manifest_validation_ok={True|False} reason={reason|None}
[handoff_wiring] active_universe_read_ok={True|False} path={path}
[handoff_wiring] accepted_candidate_count={N}
[handoff_wiring] rejected_candidate_count={N}
[handoff_wiring] fail_closed_reason={reason|None}
[handoff_wiring] scanner_fallback_attempted=False
[handoff_wiring] candidate_source=handoff_reader
[handoff_wiring] apex_input_changed=False
[handoff_wiring] scanner_output_changed=False
[handoff_wiring] risk_logic_changed=False
[handoff_wiring] order_logic_changed=False
[handoff_wiring] live_output_changed=False
```

---

## 13. Candidate Shape Compatibility Matrix

Confirmed compatibility between handoff symbol list and `run_signal_pipeline()`:

| `run_signal_pipeline()` requirement | Handoff provides |
|------------------------------------|------------------|
| `universe: list[str]` | Yes — symbol strings extracted from handoff candidates |
| Regime (unchanged) | Passed from existing `get_market_regime()` call — unchanged |
| News data | Fetched by `run_signal_pipeline()` per symbol — unchanged |
| IB connection | Passed from existing `ib` argument — unchanged |

No changes to `signal_pipeline.py`, `signals/__init__.py`, `score_universe()`, or any downstream logic.

---

## 14. Files Required for Implementation (Sprint 7E)

| File | Action | Classification |
|------|--------|---------------|
| `bot_trading.py` | Add flag conditional at line 1447 | production runtime |
| `handoff_candidate_adapter.py` | Create new — pure mapping function | adapter-only |
| `handoff_reader.py` | No changes — already complete | production runtime |
| `config.py` | No changes — flag already exists | production runtime |
| `tests/test_handoff_wiring_integration.py` | Create — full suite | production runtime test |

Do NOT touch: `scanner.py`, `signal_pipeline.py`, `signals/__init__.py`, `apex_orchestrator.py`, `guardrails.py`, `orders_core.py`, `bot_ibkr.py`.

---

## 15. Go / No-Go Criteria for Sprint 7E

Implementation must not begin until:

- [ ] Wiring point confirmed by Amit (Section 3 above)
- [ ] Candidate shape mapping approved (Section 6)
- [ ] Fail-closed behaviour approved (Section 9)
- [ ] Rollback path confirmed (Section 11)
- [ ] Implementation test plan approved (see `intelligence_first_controlled_handoff_implementation_test_plan.md`)
- [ ] Risk review accepted (see `intelligence_first_controlled_handoff_risk_review.md`)
- [ ] **Amit explicitly approves Sprint 7E implementation**
