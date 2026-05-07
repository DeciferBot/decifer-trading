# Intelligence-First: Paper vs Current Metric Reconciliation

**Sprint:** 7D  
**Date:** 2026-05-07  
**Status:** Resolved — design constraint documented

---

## The Anomaly

Sprint 7C reported:

| Metric | Value |
|--------|-------|
| Paper candidates | 50 |
| Paper ∩ current | 50 |
| In paper not current | 0 |
| Paper unique rate | 0.0 |

Earlier advisory evidence (current_vs_shadow_comparison.json) reported:

| Metric | Value |
|--------|-------|
| Shadow universe | 50 |
| True scanner pool | 235 |
| In shadow not current (scanner) | 23 |

The 23 stable missing shadow symbols are in the shadow universe but **not** in the true scanner candidate pool. If the paper handoff is derived from the shadow universe, at least some of those 23 should appear in paper-but-not-current — but Sprint 7C showed zero.

---

## Root Cause: Sprint 7C Used an Enriched Comparison Set

`paper_handoff_comparator.py` built its "current" set by combining three symbol lists from `current_vs_shadow_comparison.json`:

```python
current_syms  = overlap_symbols            # 27 — in both scanner AND shadow
current_syms |= in_current_not_shadow      # 208 — in scanner only
current_syms |= in_shadow_not_current      # 23 — in shadow only, NOT in scanner
```

By adding `in_shadow_not_current` (the 23 shadow-only symbols) to the "current" comparison set, the comparator made all 50 paper candidates appear present in "current." This masked the additive nature of the handoff.

This is **Option B** from the Sprint 7D spec: the "current" in Sprint 7C was an enriched comparison set, not the true live scanner candidate set.

---

## Correct Picture: True Scanner vs Paper

| Comparison | Value |
|-----------|-------|
| True scanner candidate pool | 235 symbols |
| — Scanner-only (not in shadow) | 208 |
| — In both scanner AND shadow | 27 |
| Paper handoff universe | 50 symbols |
| — In paper AND true scanner | 27 (the overlap symbols) |
| — In paper NOT in true scanner | 23 (shadow-only — **additions**) |
| — In true scanner NOT in paper | 208 (scanner-only — **removals**) |

### Conclusion

The future production handoff is **both additive and subtractive**:

1. **Additive (23 symbols):** Paper handoff introduces 23 governed shadow-only symbols that the live scanner does not currently discover. These are intelligence-layer candidates with thematic governance, quota allocation, and source labels.

2. **Subtractive (208 symbols):** Paper handoff removes 208 scanner-only candidates from the scoring pool. These are candidates that clear scanner thresholds but have no intelligence-layer governance.

The net effect at implementation: the live bot scores 50 symbols instead of 235, but those 50 are fully governed, quota-allocated, and carry full governance metadata (route_hint, theme_ids, risk_flags, confirmation_required, source_labels).

---

## Why Paper Unique Rate Was 0.0

The `paper_unique_rate` in Sprint 7C measures:
```
len(in_paper_not_current) / paper_count
```

Because `in_paper_not_current` was 0 (all 50 paper symbols were present in the enriched current set), the rate was 0.0. This is a metric artefact, not a property of the handoff.

Correct paper_unique_rate against true scanner:
```
23 / 50 = 0.46
```

46% of paper candidates are additions that the scanner does not currently discover.

---

## Required Metric Fix Before Implementation

The implementation dry-run comparator must use:

| Metric | Definition |
|--------|-----------|
| `true_scanner_candidates` | `overlap_symbols` ∪ `in_current_not_shadow_symbols` (235 total) |
| `paper_candidates` | candidates from `paper_active_opportunity_universe.json` (50) |
| `true_overlap` | paper ∩ true_scanner (expected: 27) |
| `additions` | paper − true_scanner (expected: 23 shadow-only symbols) |
| `removals` | true_scanner − paper (expected: 208 scanner-only symbols) |
| `addition_rate` | len(additions) / len(paper_candidates) (expected: 0.46) |
| `removal_rate` | len(removals) / len(true_scanner) (expected: 0.89) |

The Sprint 7C comparator must be patched before Sprint 7E implementation. `paper_handoff_comparator.py` must NOT include `in_shadow_not_current_symbols` in the "current" set.

---

## Implications for Handoff Wiring Design

1. **Handoff is a decisive architectural shift, not a minor tweak.** The scoring pool shrinks from 235 to 50. Apex receives 50 candidates instead of up to 50 from 235 (after cap). The cap behaviour changes.

2. **The 23 additions are the primary value proposition of Intelligence-First.** These are symbols that pass intelligence-layer governance but are invisible to the scanner. Without the handoff, they never enter Apex.

3. **The 208 removals are the primary risk.** High-scoring scanner candidates that are not yet governed will disappear on handoff cutover. Specifically: any Tier A core symbol or Tier D Apex-cap survivor that is not in the shadow universe will be absent.

4. **Quota constraint governs the 50-symbol set.** SNDK/WDC/IREN are excluded by structural quota (cap=20). This is a second constraint on additions beyond the 23 shadow-only.

5. **The 27 overlap symbols confirm pipeline alignment.** 27 symbols appear in both scanner AND shadow universe. These are the low-risk handoff candidates — they will score identically regardless of candidate source.

---

## Acceptance of Metric Definition for Future Sprints

> **Locked definition for implementation dry-run (Sprint 7E+):**
>
> "current" means the set returned by `get_dynamic_universe()` at the time of the dry-run.  
> "paper" means candidates from `paper_active_opportunity_universe.json`.  
> Shadow-only symbols must NOT be included in the "current" baseline.  
> `in_shadow_not_current_symbols` is a separate signal tracked independently.
