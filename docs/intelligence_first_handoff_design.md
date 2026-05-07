# Intelligence-First Production Handoff Design

**Sprint:** 7A
**Status:** DESIGN ONLY — not implemented
**Created:** 2026-05-06
**Gate that unlocked this:** `advisory_ready_for_handoff_design` (35 records, 1 session, all safety invariants clean)

`enable_active_opportunity_universe_handoff = False` — this flag must remain False until Sprint 7B paper-handoff implementation is approved by Amit and all cutover checklist items are verified.

---

## 1. Current State

### 1.1 Scanner-Led Pipeline (Production Today)

```
bot_trading.run_scan()
  → scanner.get_dynamic_universe()
      → Tier A: hardcoded floor symbols (SPY, QQQ, IWM, GLD, IBIT, USO + others)
      → Tier B: universe_promoter.load_promoted_universe()  (daily_promoted.json)
      → Tier C: sector rotation (hardcoded)
      → Tier D: universe_position.get_position_research_universe()  (position_research_universe.json)
  → score_universe(candidates)
  → Apex (claude-sonnet-4-6) evaluates scored candidates
  → orders_core executes approved trades
```

All candidate discovery is scanner-led. The scanner decides what symbols Apex ever sees. There is no quality filter applied to the candidate pool before scoring — tier membership alone determines inclusion.

### 1.2 Current Advisory-Only Status

The Intelligence-First layer is running in parallel as a **read-only observer**:

```
bot_trading.run_scan()
  → [all production logic above, unchanged]
  → advisory_logger.log_advisory_context(candidates, regime)  ← advisory hook only
      reads advisory_report.json (offline)
      appends one record to advisory_runtime_log.jsonl
      does not modify candidates, Apex input, risk, orders, or execution
```

The `advisory_report.json` is generated offline by `advisory_reporter.py`, which reads the shadow universe (`active_opportunity_universe_shadow.json`) and compares it against `current_pipeline_snapshot.json`.

### 1.3 Active Feature Flags

| Flag | Current Value | Effect |
|------|--------------|--------|
| `intelligence_first_advisory_enabled` | `True` | Advisory logging hook active in run_scan() |
| `enable_active_opportunity_universe_handoff` | `False` | Production candidate source unchanged |

### 1.4 Live Output Status

`live_output_changed = false` — confirmed across all 35 advisory observation records. No production decision, Apex input, order, or execution has been affected by the Intelligence-First layer.

---

## 2. Target State

When `enable_active_opportunity_universe_handoff = True` is eventually set:

```
bot_trading.run_scan()
  → [handoff reader] reads active_opportunity_universe.json
      validate schema
      validate freshness (max_age_minutes threshold)
      validate required candidate fields
      fail-closed if any check fails
  → approved candidates → score_universe(candidates)   ← same scoring engine
  → Apex (claude-sonnet-4-6) evaluates scored candidates  ← same Apex
  → orders_core executes approved trades                  ← same execution
```

### What changes:
- **Candidate source**: `scanner.get_dynamic_universe()` is replaced (in the live path) by reading `active_opportunity_universe.json`
- **Candidate quality floor**: every candidate must have `symbol`, `reason_to_care`, `route`, `source_labels` — no unlabelled symbols enter scoring
- **Discovery authority**: the Universe Builder (offline) is the sole symbol discovery engine; the live bot is a consumer

### What does not change:
- Scoring engine (`score_universe`)
- Apex call, prompt, model, or input format
- Risk engine
- Position sizing
- Order logic
- Execution
- IBKR connection
- Manual and held protections
- Options execution path
- EOD flat logic
- Any guardrail

---

## 3. Explicit Non-Goals

This handoff must never:

- Call a broker
- Submit, modify, or cancel any order based on Intelligence-First logic
- Change position sizing
- Change risk thresholds or guardrails
- Call an LLM for symbol discovery
- Scrape raw news
- Run a broad intraday scan
- Allow the Intelligence Layer to add symbols outside the approved universe
- Allow Apex to discover new symbols
- Allow candidates without `reason_to_care`
- Allow candidates without `route`
- Allow candidates without `source_labels`
- Allow any `executable=true` flag to propagate from the intelligence file to any execution path
- Create a second live bot alongside the existing one
- Add duplicate scoring, risk, or routing logic

---

## 4. Production Handoff Contract

When `enable_active_opportunity_universe_handoff = True`:

### Allowed:

| Action | Who |
|--------|-----|
| Read `active_opportunity_universe.json` | Handoff reader (new, production_runtime) |
| Validate schema and freshness | Handoff reader |
| Pass `symbol`, `reason_to_care`, `route`, `source_labels` to scoring | Handoff reader |
| Preserve manual conviction protection | Handoff reader / existing logic |
| Preserve held position protection | Existing orders_state logic — unchanged |
| Pass curated candidate list to `score_universe()` | Existing production path — unchanged |
| Score, filter, Apex-evaluate, and execute | Existing production path — unchanged |

### Forbidden:

| Action | Why |
|--------|-----|
| Fallback to `scanner.get_dynamic_universe()` if handoff file missing | Fail-closed — no silent scanner restoration |
| Fallback to LLM symbol discovery | LLM cannot be a fallback discovery engine |
| Fallback to raw news for candidates | Raw news is not an approved source |
| Fallback to broad intraday scan | Not an approved source |
| Allow candidates without `reason_to_care` | Required field — enforced at schema validation |
| Allow candidates without `route` | Required field — enforced at schema validation |
| Allow candidates without `source_labels` | Required field — enforced at schema validation |
| Read `executable=true` from any intelligence file | Intelligence layer is advisory-only |
| Read `order_instruction` from any intelligence file | Intelligence layer is advisory-only |
| Pass unvalidated candidates to scoring | All candidates must pass schema check first |
| Allow candidates outside the approved roster | Approved-source guard enforced |

---

## 5. Fail-Closed Behaviour

**Definition:** If the handoff file cannot be consumed safely, no trades are entered. The bot operates in a zero-new-entry mode (existing positions continue to be managed by PM review). No fallback discovery of any kind occurs.

### Fail-closed triggers:

| Condition | Action |
|-----------|--------|
| `active_opportunity_universe.json` missing | No new entries. Log: `HANDOFF_FILE_MISSING` |
| File stale (age > `max_age_minutes`) | No new entries. Log: `HANDOFF_FILE_STALE` |
| Schema invalid (top-level structure wrong) | No new entries. Log: `HANDOFF_SCHEMA_INVALID` |
| Any candidate missing `symbol` | Reject that candidate. Log: `CANDIDATE_MISSING_SYMBOL` |
| Any candidate missing `reason_to_care` | Reject that candidate. Log: `CANDIDATE_MISSING_REASON_TO_CARE` |
| Any candidate missing `route` | Reject that candidate. Log: `CANDIDATE_MISSING_ROUTE` |
| Any candidate missing `source_labels` | Reject that candidate. Log: `CANDIDATE_MISSING_SOURCE_LABELS` |
| Any candidate with `executable=true` | Reject entire file. Log: `EXECUTABLE_FLAG_VIOLATION` |
| Any candidate outside the approved universe | Reject that candidate. Log: `CANDIDATE_NOT_IN_APPROVED_UNIVERSE` |
| Risk config missing or invalid | No new entries (existing risk-config fail behaviour) |
| Zero candidates survive validation | No new entries. Log: `HANDOFF_ZERO_VALID_CANDIDATES` |

### Fail-closed means:
- **No trade**
- **No fallback discovery** — not scanner, not LLM, not raw news, not broad scan
- **Existing positions continue** under PM review (Track B path unchanged)
- **Forced exits still execute** (EOD flat, timeout exits — these are deterministic and do not depend on candidate source)
- **Log the reason** — every fail-closed trigger must produce a structured log entry

---

## 6. Unresolved Current Candidates Policy

### Advisory evidence:
- 1,316 / 1,920 candidate evaluations (68.5%) are `advisory_unresolved` — current-pipeline candidates with no advisory report entry
- 20 symbols appear as unresolved in ≥18/35 records (GOOGL, VXX, RKLB, SNDK, IONQ, ORCL, NKE, WDC, MSTR, SNAP, RGTI, SLV, SMCI, CRDO, UNH, UAMY, EOSE, GE, AMAT, INTC)
- MSFT unresolved 6/6 times when appearing

### Options considered:

| Option | Description | Risk |
|--------|-------------|------|
| A | Drop all unresolved candidates at handoff | High — legitimate current candidates disappear |
| B | Admit only if they appear in an approved source label | Conservative — admission requires explicit endorsement |
| C | Route all unresolved to watchlist-only pending review | Medium — keeps them visible, excludes from execution |
| D | Create a migration exception list with expiry | Complex — requires ongoing maintenance |

### **Recommended policy: Option B — approved source label required**

A symbol enters the handoff universe only if it appears in at least one approved source label in `active_opportunity_universe.json`. Approved source labels are:
- `favourites_manual_conviction`
- `tier_a_always_on`
- `committed_universe_read_only`
- `economic_candidate_feed`
- `overnight_research_read_only`
- `legacy_theme_tracker_read_only`
- `position_research_universe`
- `universe_promoter` / `daily_promoted`

Symbols that are in the current scanner pool but have **no** entry in the handoff file (i.e., they were never assigned a `reason_to_care` or `source_label`) are **not eligible**. They are not dropped — they are simply not admitted until the Universe Builder covers them.

**Rationale:** The 68.5% unresolved rate reflects that the advisory report covers only the shadow universe (50 candidates). After handoff, the approved universe will cover far more symbols. The unresolved rate is an artefact of the pre-handoff gap, not a signal about candidate quality.

**Effect on MSFT:** MSFT will be eligible if the Universe Builder adds it with a valid `reason_to_care` and `source_label`. Until then, it does not enter the handoff candidate pool. No manual exception list needed.

---

## 7. Missing Shadow Candidates Policy

### Advisory evidence:
- 23 symbols in shadow but consistently missing from current pipeline
- All 23 are stable across all 35 records: VRT, ETN, PWR, CEG, XLU, TSM, AVGO, ASML, SMH, BAC, WFC, XLF, SLB, XLE, LMT, NOC, RTX, GD, ITA, QUAL, XLP, XLV, SPLV
- Mix: defensive ETFs (XLU, XLE, XLF, XLP, XLV), semiconductor leaders (TSM, AVGO, ASML, SMH), financials (BAC, WFC), defence (LMT, NOC, RTX, GD, ITA), factor ETFs (QUAL, SPLV)

### Policy:

- These 23 symbols are **eligible** for the handoff universe if the Universe Builder assigns them a valid `reason_to_care`, `route`, and `source_labels`
- They do **not** require current scanner presence to enter the handoff universe — the handoff replaces the scanner, not augments it
- They remain **non-executable** until the live bot's full entry/risk checks pass (same as any other candidate)
- The Universe Builder is the authority on whether they are assigned — no manual list, no special exception path
- The advisory evidence establishes that these symbols exist in the intelligence layer but haven't been admitted by the scanner; post-handoff, the intelligence layer is the source, so this gap self-resolves if the Universe Builder covers them

---

## 8. Tier D Policy

### Advisory evidence:
- 150 Tier D candidates in current pipeline
- 5 preserved in shadow (AMD, ASTS, MU, NBIS, NVDA — all via manual/economic sources, not Tier D alone)
- 145 excluded by structural quota cap (20)
- Preservation rate: 3.3%
- Structural quota binding on 35/35 records

### Policy:

**Tier D becomes a source label, not an organising principle.**

| Old model | New model |
|-----------|-----------|
| Tier D = a tier with guaranteed universe presence | `position_research_universe` = one approved source label among several |
| Tier D symbols compete via `apex_cap_score.py` bonus | Structural candidates compete via `quota_allocator.py` route-aware slots |
| 150 Tier D symbols enter scoring pool | Only symbols with a valid `reason_to_care` + structural route enter the handoff file |
| Scanner constructs Tier D universe | Universe Builder reads `position_research_universe.json` as one input |

**Structural quota cap:** The 20-slot structural cap is **not changed in Sprint 7A**. Advisory evidence shows 180 structural candidates competing for 20 slots. The ranking quality of the overflow must be reviewed before any cap change is considered.

**What this means for REG-001 (`apex_cap_score.py`):** Once quota allocator provides structural protection via route-aware slots, the score bonus is no longer the primary Tier D protection mechanism. REG-001 retirement condition advances one step closer but is not yet met (production handoff not yet stable).

---

## 9. Route Disagreement Policy

### Advisory evidence (35 records, 277 disagreement evaluations):

| Disagreement type | Count | Classification |
|-------------------|-------|----------------|
| `intraday_swing → manual_conviction` | 134 (48%) | Vocabulary only |
| `intraday_swing → swing` | 82 (30%) | Naming delta — potentially meaningful |
| `position → manual_conviction` | 41 (15%) | Vocabulary only |
| `intraday_swing → watchlist` | 20 (7%) | Real route conflict |

~63% vocabulary, ~37% meaningful.

### Policy:

**Route vocabulary must be harmonised before handoff implementation.**

1. **`manual_conviction` route:** The shadow universe uses a dedicated `manual_conviction` quota group. The current pipeline routes these symbols as `position` or `intraday_swing`. Before handoff, either:
   - The handoff reader must map `manual_conviction → position` or `intraday_swing` (per symbol state), OR
   - The route vocabulary must be aligned so `manual_conviction` is a valid route in both systems

2. **`intraday_swing → swing`:** This is a naming difference. `route_tagger.py` uses `intraday_swing`; the shadow universe may assign `swing`. The handoff reader must normalise these before passing candidates to scoring.

3. **`intraday_swing → watchlist`:** These 20 disagreements represent symbols the shadow model downgrades to watchlist that the current pipeline routes to execution tier. These are the most important disagreements to resolve — they represent candidates the intelligence layer wants to suppress. Post-handoff, the Universe Builder's route assignment governs; if a symbol is assigned `watchlist`, it should not be routed to the Apex execution evaluation.

**Authority:** Route changes occur in the Universe Builder, not in the live bot execution path. Apex cannot rewrite routes. The handoff reader must pass routes as-is from the universe file; any normalisation is the handoff reader's responsibility.

---

## 10. Manual and Held Policy

### Manual conviction:
- All manual conviction symbols (current: ASTS, GLD, IBIT, USO, SPY, QQQ, NVDA, TSLA, AAPL, HIMS, NBIS, MU, ONDS) are protected
- Protection means they **enter the handoff candidate pool** even if the Universe Builder assigns them `route=manual_conviction`
- Protection does **not** mean they are executable — they still pass through scoring, Apex evaluation, and risk checks
- `favourites.json` remains the source of truth for manual conviction; the Universe Builder reads it as an input

### Held positions:
- Any symbol with an open position in IBKR is protected by existing `orders_state.py` held logic — this is **never read from an intelligence file**
- Held protection does not come from the handoff universe; it is enforced at the order submission layer regardless of candidate source
- The intelligence layer must **not** be in the held-protection path

### Combined rule:
The handoff universe may inform which candidates are admitted; it must not be the authority on which positions are protected. Held and manual protection are runtime state decisions, not advisory recommendations.

---

## 11. Apex Policy

### What Apex can do post-handoff (unchanged from current):
- Evaluate candidate actionability from curated list
- Reject, defer, or downgrade candidates
- Apply regime context to entry decisions
- Evaluate PM TRIM/EXIT/HOLD on existing positions

### What Apex cannot do (enforced at handoff contract level):
- Discover new symbols not in the handoff file
- Create themes or add to the approved universe
- Rewrite or override the `route` assigned by the Universe Builder
- Convert `attention` candidates to `position` thesis unilaterally
- Suppress `structural` candidates because of 5-minute price movement (this is an existing constraint, not new)
- Generate `executable=true` instructions from the intelligence layer input

**Apex receives curated candidates only.** The handoff reader is the gatekeeper. If the handoff file is invalid, Apex receives no new-entry candidates (fail-closed — PM review of existing positions continues).

---

## 12. Rollback Policy

### Design principle: flag off must be instant and total.

| Component | Rollback action |
|-----------|----------------|
| `enable_active_opportunity_universe_handoff` | Set to `False` in `config.py` — live bot reverts to `scanner.get_dynamic_universe()` immediately on restart |
| `intelligence_first_advisory_enabled` | Can remain `True` or be set `False` independently — advisory logging is independent of handoff |
| Old scanner path (`scanner.py`) | Never removed during Sprint 7A–7C; must remain functional and tested |
| `active_opportunity_universe.json` | File can be left in place; it is ignored when flag is False |
| Code revert | Must not be required — rollback is flag-only |

**Invariant:** No code revert should ever be required to roll back the handoff. If flag-off does not fully restore pre-handoff behaviour, that is a design bug that must be fixed before production handoff is approved.

---

## 13. Observability

Required logs for the production handoff path:

| Event | Log key | Level |
|-------|---------|-------|
| Handoff file read attempt | `HANDOFF_READ_ATTEMPT` | INFO |
| Handoff file missing | `HANDOFF_FILE_MISSING` | ERROR |
| Handoff file stale | `HANDOFF_FILE_STALE` | WARNING |
| Schema invalid | `HANDOFF_SCHEMA_INVALID` | ERROR |
| Candidate rejected: missing field | `CANDIDATE_REJECTED_MISSING_{FIELD}` | WARNING |
| Candidate rejected: not in approved universe | `CANDIDATE_REJECTED_NOT_APPROVED` | WARNING |
| Executable flag violation | `EXECUTABLE_FLAG_VIOLATION` | ERROR |
| Zero valid candidates after validation | `HANDOFF_ZERO_VALID_CANDIDATES` | ERROR |
| Route disagreement detected | `HANDOFF_ROUTE_DISAGREEMENT` | INFO |
| Quota pressure | `HANDOFF_QUOTA_PRESSURE` | INFO |
| Source collision | `HANDOFF_SOURCE_COLLISION` | DEBUG |
| Fail-closed triggered | `HANDOFF_FAIL_CLOSED` | ERROR |
| `live_output_changed` | `LIVE_OUTPUT_UNCHANGED` (always) | DEBUG |

All log entries must be structured (key=value or JSON) for log aggregator compatibility.

---

## 14. Cutover Phases

| Phase | Name | Description | Status |
|-------|------|-------------|--------|
| 0 | **Advisory-only** | Current state. Advisory logging active. Handoff flag False. No production change. | **Active** |
| 1 | **File validation only** | Handoff reader validates `active_opportunity_universe.json` on each scan cycle but does not consume candidates. Logs validation status only. Flag still False. | Not started |
| 2 | **Paper path only** | Handoff file consumed as candidate source in paper/shadow path only. Live bot still uses scanner. Both paths run and their outputs are compared. | Not started |
| 3 | **Dry-run candidate source** | Handoff file is the candidate source in a shadow dry run alongside the live bot. Apex receives curated candidates. Orders are not submitted. Full route/scoring/risk comparison logged. | Not started |
| 4 | **Controlled production switch** | `enable_active_opportunity_universe_handoff = True`. Curated universe is the live candidate source. Advisory logging remains active for regression monitoring. Rollback flag is tested. | Not started |
| 5 | **Retirement of scanner-led path** | Old `scanner.get_dynamic_universe()` path removed from live bot. `test_scanner.py` universe composition tests updated. `current_pipeline_snapshot.json` retired. | Not started |

**Phase 0 → Phase 1 gate:** Sprint 7B design review approved by Amit.
**Phase 1 → Phase 2 gate:** Handoff validation tests pass (Sprint 7B test suite).
**Phase 2 → Phase 3 gate:** Paper comparison shows structural equivalence over ≥5 sessions.
**Phase 3 → Phase 4 gate:** Dry-run comparison shows equivalent or better Apex decision quality over ≥3 sessions.
**Phase 4 → Phase 5 gate:** Production handoff stable for ≥10 sessions; rollback tested; no regressions.

**No phase is entered without Amit approval.**
