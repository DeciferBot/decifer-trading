# Intelligence-First Scanner-Only Removals Review Plan

**Sprint:** 7H.1 — Operations readiness
**Status:** Pre-activation review plan. No code changes. No flag flip. Review is conducted as a design/advisory exercise before any activation decision.
**Classification:** Advisory/design document. No production code changed.
**Context:** When `enable_active_opportunity_universe_handoff = True`, the live bot's Track A candidate source switches from Alpaca scanner discovery to the handoff publisher's `active_opportunity_universe.json`. Any symbol that appears in the scanner path but is absent from the shadow universe will disappear from Track A. This review plan documents how to identify, categorise, and evaluate those removals before activation.

---

## 1. Purpose

The handoff universe is a curated, governance-filtered set of candidates derived from the shadow universe (`active_opportunity_universe_shadow.json`). The Alpaca scanner discovers candidates dynamically each cycle based on momentum, volume, and signal scores. These two sources will never be identical.

Before activation, the operator must understand:

1. **How many scanner-only symbols exist** — symbols that appear in the current scanner path but are absent from the shadow universe
2. **Why they are absent** — structural quota exclusion, theme filter, conviction threshold, or simply not in the committed universe
3. **Whether their absence is acceptable** — do they represent alpha the handoff universe would miss?
4. **Whether the absence is systematic** — is there a category of symbol (sector, size, regime type) consistently excluded?

This review does not block activation if the removals are understood and accepted. It blocks activation if removals reveal a systematic gap that invalidates the handoff as a representative substitute for the scanner path.

---

## 2. Known Context (as of Sprint 7H.1)

The following is known prior to conducting the review:

| Fact | Source |
|------|--------|
| Shadow universe candidate count: 50 | `active_opportunity_universe_shadow.json` |
| Scanner-discovered universe size: varies per cycle (typically 50–80 candidates after filtering) | Scanner path in `bot_trading.py` |
| Estimated scanner-only removals: ~208 symbols across recent scan cycles | Sprint 7H design review, Section 4 (Risk-02) |
| Structural_position quota currently full at 20 | Observer report structural quota analysis |
| SNDK, WDC, IREN excluded from shadow due to structural quota | Confirmed via economic_candidate_feed analysis |
| Shadow universe: 43 economic candidates + structural position quota | `economic_candidate_feed.json` + quota registry |
| `handoff_enabled = false` in manifest: advisory-only; `executable = false` on all candidates | All sprint validations |

**Important caveat:** The 208 figure is a cumulative count across cycles — not unique symbols. The unique scanner-only symbol count may be substantially lower. The review will establish the true unique count.

---

## 3. Review Categories

Each scanner-only symbol should be placed in exactly one category:

| Category | Definition | Activation implication |
|----------|-----------|----------------------|
| **A — Structural quota excluded** | Symbol is in the economic candidate feed but excluded from shadow universe because the structural_position quota is full | Acceptable if quota design is intentional; document the trade-off |
| **B — Not in economic feed** | Symbol is not in `economic_candidate_feed.json` at all — pure scanner discovery (Alpaca momentum/volume screen with no economic theme) | Evaluate: are these high-quality or noise? |
| **C — In economic feed, below conviction threshold** | Symbol is in the feed but scored below the shadow universe admission threshold | Acceptable; threshold exists for a reason |
| **D — Theme exclusion** | Symbol is excluded by `theme_overlay_map.json` or `transmission_rules` | Acceptable if theme exclusion is correct |
| **E — Committed universe miss** | Symbol is not in the committed universe (top-1000 by dollar volume, weekly refresh) | Investigate: is the committed universe refresh stale? |
| **F — Governance gap** | Symbol should be in the shadow universe but is absent due to a gap in the pipeline | Must be fixed before activation; this is a defect category |

---

## 4. Review Inputs

The review requires the following inputs:

| Input | Source | How to obtain |
|-------|--------|--------------|
| Current scanner path candidates | Run one scanner-only scan cycle (flag=False) and capture the `universe` variable at the wiring point | Bot log with DEBUG level enabled, or add a one-shot diagnostic print |
| Shadow universe candidates | `data/universe_builder/active_opportunity_universe_shadow.json` → `candidates[]` → `symbol` fields | Direct file read |
| Economic candidate feed | `data/intelligence/economic_candidate_feed.json` → `candidates[]` → `symbol` fields | Direct file read |
| Committed universe | `data/reference/symbol_master.json` or equivalent | Direct file read |
| Structural quota registry | Observer report `structural_quota_advisory` section | Run `python3 handoff_publisher_observer.py` |
| Theme overlay | `data/reference/theme_overlay_map.json` | Direct file read |

**Diff construction:**

```python
scanner_symbols = set(scanner_universe)  # from one cycle
shadow_symbols = set(s["symbol"] for s in shadow_universe["candidates"])
scanner_only = scanner_symbols - shadow_symbols
shadow_only = shadow_symbols - scanner_symbols  # symbols added by handoff not in scanner
both = scanner_symbols & shadow_symbols
```

The primary focus is `scanner_only`. `shadow_only` is noted for completeness (handoff adds symbols the scanner would miss).

---

## 5. Output Design

The review produces a structured report with the following fields. This is not a code artifact — it is a human-readable document section, to be filled in when the review is conducted.

```
SCANNER-ONLY REMOVALS REVIEW
Conducted: [date]
Flag state at review: False (validation-only)

COUNTS
  scanner_universe_size: [N]
  shadow_universe_size: [N — should be 50]
  scanner_only_count: [N]
  shadow_only_count: [N]
  overlap_count: [N]
  overlap_pct: [N]%

CATEGORY BREAKDOWN
  A — Structural quota excluded: [N symbols]
  B — Not in economic feed: [N symbols]
  C — Below conviction threshold: [N symbols]
  D — Theme exclusion: [N symbols]
  E — Committed universe miss: [N symbols]
  F — Governance gap (defect): [N symbols]

NOTABLE SCANNER-ONLY SYMBOLS
  [Symbol]: [category] — [1-line reason]
  [Symbol]: [category] — [1-line reason]
  ...

NOTABLE SHADOW-ONLY SYMBOLS (added by handoff)
  [Symbol]: [category] — [1-line reason]
  ...

STRUCTURAL QUOTA PRESSURE
  structural_position quota at capacity: [true/false]
  symbols excluded by quota: [list]
  quota design assessment: [acceptable / review required]

ACTIVATION ASSESSMENT
  Governance gap (Category F) count: [N]
  Any Category F defects: [yes / no]
  Review conclusion: [acceptable / requires fix / requires Amit review]
  Reviewer: [name]
  Approved by Amit: [yes / no / pending]
```

**Activation gate from this review:**

| Review outcome | Action |
|----------------|--------|
| Category F = 0; all removals in A–E with understood reasons | Acceptable to proceed to activation |
| Category F > 0 | Must fix governance gap before activation; file defect |
| Category B symbols are disproportionately high-conviction | Review shadow universe admission criteria with Amit |
| Structural quota excludes symbols Amit considers essential | Review quota design before activation |
| Amit review required | Stop. Do not activate until Amit approves review findings |
