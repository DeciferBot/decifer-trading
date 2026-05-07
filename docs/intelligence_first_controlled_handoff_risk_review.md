# Intelligence-First: Controlled Handoff Risk Review

**Sprint:** 7D  
**Date:** 2026-05-07  
**Status:** Design only — not implemented  
**Blocked on:** Amit approval before Sprint 7E implementation

---

## 1. Purpose

Document every material risk introduced by wiring `handoff_reader.py` into `bot_trading.py` at the `get_dynamic_universe()` call site. For each risk: what goes wrong, what the observable symptom is, what the mitigation is, and which test covers it. This review must be approved before Sprint 7E implementation begins.

---

## 2. Risk Matrix

### RISK-01 — Wrong wiring point changes execution path

| Field | Detail |
|-------|--------|
| **Description** | The flag conditional is placed at the wrong location in `bot_trading.py` — not at `get_dynamic_universe()` but at a point that bypasses regime detection, scoring, guardrails, or order submission. |
| **Symptom** | Scored dicts shape changes; guardrails receive wrong input; Apex prompt changes unexpectedly. |
| **Likelihood** | Low — wiring point is precisely identified as `bot_trading.py:1447`. |
| **Impact** | High — if wiring is too deep it could bypass risk controls; if too shallow it could prevent regime detection from running. |
| **Mitigation** | Wiring point confirmed as `bot_trading.py:1447` where `get_dynamic_universe(ib, regime)` is called. The replacement returns `list[str]` — identical type. All downstream code (`run_signal_pipeline`, `score_universe`, guardrails, Apex) receives same input type. Wiring is a single conditional branch; no surrounding logic changes. |
| **Test coverage** | Group 1 (flag=False path unchanged), Group 6 (Apex boundary: input shape identical to pre-implementation), Group 7 (risk/order/execution unchanged). |

---

### RISK-02 — Accidental scanner fallback when handoff fails

| Field | Detail |
|-------|--------|
| **Description** | A fail-closed event occurs (manifest missing, expired, invalid) but the code falls through and calls `get_dynamic_universe()` instead of skipping the cycle. |
| **Symptom** | Bot executes new entries when it should have skipped them; `scanner_fallback_attempted` logged incorrectly as True; `fail_closed_reason` not set. |
| **Likelihood** | Medium — easy to accidentally write `if universe is None: universe = get_dynamic_universe(...)`. |
| **Impact** | Critical — this is the core fail-closed invariant. A scanner fallback means intelligence-layer candidates do not gate the trade, defeating the entire handoff architecture. |
| **Mitigation** | Explicit design: on `_get_handoff_symbol_universe()` returning `None`, call `_log_handoff_fail_closed(reason=...)` and `return` from scan cycle. No fallback branch exists. Scanner is not called when flag is True, regardless of outcome. Log entry `scanner_fallback_attempted=False` required in all cases. |
| **Test coverage** | Group 3 (all fail-closed scenarios: tests 3.1–3.9 confirm scanner NOT called after fail-closed); Group 4 (all 21 Sprint 7B conditions). |

---

### RISK-03 — Invalid candidate shape passed to `run_signal_pipeline()`

| Field | Detail |
|-------|--------|
| **Description** | `_get_handoff_symbol_universe()` returns something other than `list[str]` — e.g. `list[dict]`, empty list with trailing None, or list with non-string elements. |
| **Symptom** | `run_signal_pipeline()` raises TypeError or produces corrupt scored dicts; scoring fails silently for some symbols. |
| **Likelihood** | Low — `run_signal_pipeline()` input type is `list[str]` and the adapter is designed to extract symbol strings. |
| **Impact** | High — signal scoring fails for affected symbols; Apex receives partial or empty candidate list. |
| **Mitigation** | Symbol extraction is explicit: `[c["symbol"] for c in accepted_candidates]`. Only string values are extracted. Type is guaranteed by the candidate validator (each candidate must have a `symbol` field). If extraction yields empty list, fail closed (RISK-07). Governance map populated separately — symbol list stays clean. |
| **Test coverage** | Group 2.6 (symbol list extracted correctly), Group 2.8 (scoring receives same input type), Group 4.13 (missing `symbol` → candidate rejected), Group 5 (adapter pure function tests). |

---

### RISK-04 — Apex receives empty candidate list

| Field | Detail |
|-------|--------|
| **Description** | All handoff candidates fail per-candidate validation, or the governance map is empty, resulting in an empty list passed to `run_signal_pipeline()`, which passes an empty scored list to Apex. |
| **Symptom** | Apex Track A receives no candidates; no new entries possible for the session. |
| **Likelihood** | Low in stable operation; possible if governance files are corrupt or all candidates fail validation. |
| **Impact** | Medium — no new entries for the cycle. Existing positions managed by Track B are unaffected. |
| **Mitigation** | Zero accepted candidates triggers fail-closed (Group 4.21 / RISK-07 mitigation), not an empty Apex call. Bot skips new entry evaluation entirely when no valid handoff candidates exist. PM Track B continues independently for existing positions. |
| **Test coverage** | Group 3.7 (zero accepted candidates → fail closed), Group 4.21 (same), Group 3.10 (PM logic runs after fail-closed). |

---

### RISK-05 — `handoff_reader.py` exception blocks bot scan cycle

| Field | Detail |
|-------|--------|
| **Description** | An unhandled exception inside `_get_handoff_symbol_universe()` (from `handoff_reader.load_production_handoff()`) propagates up and crashes `run_scan()`. |
| **Symptom** | Bot scan cycle terminates abnormally; existing PM logic and position management do not run for that cycle. |
| **Likelihood** | Low — `handoff_reader.py` has comprehensive error handling built in Sprint 7B. |
| **Impact** | High — if bot crashes, positions are unmanaged for the scan cycle duration. |
| **Mitigation** | `_get_handoff_symbol_universe()` wraps the entire `handoff_reader.load_production_handoff()` call in a `try/except Exception`. On exception: logs full traceback, calls `_log_handoff_fail_closed(reason="handoff_reader_exception")`, returns None. The fail-closed path then skips new entries without crashing. PM Track B is outside this branch and unaffected. |
| **Test coverage** | Group 3.8 (handoff reader exception → fail closed; bot continues; PM path unaffected). |

---

### RISK-06 — Valid paper file consumed in production (stale governance data)

| Field | Detail |
|-------|--------|
| **Description** | `data/live/current_manifest.json` is pointed at a paper-mode snapshot that is technically valid (passes all Sprint 7B checks) but reflects governance data from a previous session. Live bot consumes governance candidates that are hours or days stale. |
| **Symptom** | Bot enters positions based on outdated thematic analysis; symbols that have lost their thesis are still scored. |
| **Likelihood** | Medium — possible if the manifest publisher runs infrequently or if the `expires_at` field is set too generously. |
| **Impact** | Medium — risk is a stale thesis, not a structural failure. The signal engine still scores symbols on current market data; the governance metadata (theme, route) may be outdated. |
| **Mitigation** | Manifest expiry check is enforced by `handoff_reader.validate_manifest()` (Sprint 7B fail-closed condition 3: manifest expired → fail closed). Active universe file has its own `expires_at`. Both are validated before any symbol is used. The Handoff Publisher must set a conservative expiry (production: 24h max for paper, to be tightened for live). |
| **Test coverage** | Group 4.3 (manifest expired → fail closed), Group 4.10 (active universe expired → fail closed). |

---

### RISK-07 — Structural quota excludes all newly governed symbols

| Field | Detail |
|-------|--------|
| **Description** | All 23 additive shadow-only symbols (the primary value proposition of Intelligence-First) are excluded by structural quota pressure, resulting in an effective handoff of 27 scanner-overlap symbols only — no net addition vs scanner. |
| **Symptom** | `accepted_candidate_count` = 27; all 23 additions are in `rejected_candidate_count`; handoff provides no uplift over scanner. |
| **Likelihood** | Low — the quota constraint is enforced in the Universe Builder (upstream), not in `handoff_reader.py`. The 50-symbol active universe is already the quota-resolved set. The 23 additions are already inside the 50. |
| **Impact** | Medium — if this occurs due to a quota misconfiguration, intelligence value is lost but the bot is not broken. |
| **Mitigation** | The active universe produced by the Universe Builder already encodes quota decisions. The Handoff Publisher validates that accepted candidates include the expected additions before publishing. SNDK/WDC/IREN exclusion from paper is a documented quota constraint (structural full at 20), not a future risk — it is the current known state. The 23 additions in paper ARE inside the 50-symbol set (they are shadow-only candidates that cleared the universe's quota). |
| **Test coverage** | Group 2.4 (all candidates validated), Group 2.6 (symbol list extracted from accepted candidates only), Group 6.1–6.2 (Apex contains only accepted handoff symbols). |

---

### RISK-08 — Unresolved current candidates disappear without governance coverage

| Field | Detail |
|-------|--------|
| **Description** | 208 scanner-only candidates are removed from the scoring pool on handoff cutover. High-scoring Tier A core symbols or Tier D Apex survivors that are not in the shadow universe become completely invisible to Apex. |
| **Symptom** | Symbols that previously generated profitable entries no longer appear as candidates; Apex cannot act on setups that the signal engine would score highly. |
| **Likelihood** | High — this is a documented architectural consequence of the handoff (208 removals documented in metric reconciliation). |
| **Impact** | High — this is the primary risk of the handoff. Any symbol not in the governed universe is invisible. |
| **Mitigation** | This is the defined and accepted consequence of Intelligence-First. The governed set (50) replaces the scanner set (235) deliberately. Mitigations: (1) paper mode de-risks by using paper account only; (2) 27 overlap symbols confirm the pipeline captures the highest-signal scanner candidates; (3) rollback is one flag flip (RISK-10); (4) dry-run compare mode (`enable_handoff_dry_run_compare=True`) allows side-by-side observation before cutover; (5) Amit explicitly approves the 208-removal consequence before Sprint 7E implementation. |
| **Test coverage** | Group 2.5 (scanner NOT called when flag True), Group 6.3 (Apex cannot receive scanner-only candidates when flag True), Group 8 (rollback tests). |

---

### RISK-09 — Route vocabulary mismatch between handoff and scored dict

| Field | Detail |
|-------|--------|
| **Description** | Route values from the handoff candidate (`position`, `swing`, `intraday`, `watchlist`) conflict with or overwrite route fields already in the scored dict, producing unexpected Apex prompt context. |
| **Symptom** | Apex receives incorrect route hints; PM review misclassifies candidates; route-dependent guardrail logic fires incorrectly. |
| **Likelihood** | Low — governance fields are attached with explicit `handoff_` prefix, preventing field collision with existing scored dict fields. |
| **Impact** | Medium — route mismatch could cause Apex to defer valid entries or accept invalid ones. |
| **Mitigation** | All governance fields are prefixed `handoff_*` (e.g. `handoff_route`, `handoff_route_hint`). These are additive fields — they never overwrite existing scored dict fields (`score`, `raw_score`, `route`, signal dimensions). The existing `route` field in scored dicts comes from the signal engine and is not modified by the adapter. Apex has explicit visibility into both the signal-engine route and the governance route hint. |
| **Test coverage** | Group 5.2 (route preserved from handoff with `handoff_route` prefix), Group 5.8 (adapter does not modify score/raw_score/signal dimensions), Group 6.4 (Apex receives governance metadata fields with `handoff_` prefix). |

---

### RISK-10 — Live bot imports offline-only modules at production runtime

| Field | Detail |
|-------|--------|
| **Description** | `bot_trading.py` imports `handoff_candidate_adapter.py`, which in turn imports offline modules (`advisory_reporter.py`, `backtest_intelligence.py`, `reference_data_builder.py`, etc.), increasing production startup time and violating the production isolation contract. |
| **Symptom** | Bot startup time increases; memory footprint increases; production isolation contract violated (offline modules running in production container). |
| **Likelihood** | Low if design contract is respected — `handoff_candidate_adapter.py` is classified adapter-only; its import graph must be validated. |
| **Impact** | Medium — does not directly cause wrong trades, but violates the architecture boundary and could cause import failures if optional dependencies are missing in production containers. |
| **Mitigation** | `handoff_candidate_adapter.py` is adapter-only: no imports of scanner, orders, bot_trading, LLM, advisory reporter, backtest, or reference data builder. Import graph verified by AST check in tests (same pattern as `advisory_logger.py` verification in Sprint 6B). Only imports: stdlib (copy, typing) and project modules that are production-safe. |
| **Test coverage** | Group 5.9 (adapter is pure — no I/O, no side effects); test should include AST import verification confirming no offline module imports. |

---

### RISK-11 — Fail-open bug: handoff path proceeds despite a false `handoff_allowed`

| Field | Detail |
|-------|--------|
| **Description** | A code error causes `_get_handoff_symbol_universe()` to return a symbol list even when `handoff_reader` returns `handoff_allowed=False` (e.g. due to a boolean check inversion or missing return statement). |
| **Symptom** | Bot enters new positions using a handoff that the reader explicitly disallowed; `fail_closed_reason` is not set; `scanner_fallback_attempted` is incorrectly False. |
| **Likelihood** | Low — but the consequence is critical: unvalidated candidates entering Apex. |
| **Impact** | Critical — positions entered without valid governance provenance. This is exactly what the fail-closed design is meant to prevent. |
| **Mitigation** | Explicit check: `if not result["handoff_allowed"]: return None`. Code path requires positive assertion of `handoff_allowed=True` to proceed; any other value triggers fail-closed. All 21 Sprint 7B conditions that set `handoff_allowed=False` must be reproduced in the integration tests that check `_get_handoff_symbol_universe()`. |
| **Test coverage** | Group 4 (all 21 fail-closed conditions mapped to `handoff_allowed=False` outcomes), Group 3.9 (scanner NOT called after any fail-closed event). |

---

## 3. Risk Summary Table

| Risk | Likelihood | Impact | Mitigation Status |
|------|-----------|--------|-------------------|
| RISK-01 Wrong wiring point | Low | High | Mitigated — wiring point confirmed at line 1447 |
| RISK-02 Scanner fallback | Medium | Critical | Mitigated — explicit return; no fallback branch |
| RISK-03 Invalid candidate shape | Low | High | Mitigated — explicit string extraction; validated |
| RISK-04 Empty Apex list | Low | Medium | Mitigated — fail closed before empty Apex call |
| RISK-05 Exception blocks bot | Low | High | Mitigated — try/except; fail closed; PM unaffected |
| RISK-06 Stale paper file | Medium | Medium | Mitigated — `expires_at` enforced by manifest validator |
| RISK-07 Quota excludes additions | Low | Medium | Mitigated — quota resolved upstream in Universe Builder |
| RISK-08 Candidate disappearance | High | High | Accepted — documented consequence; rollback available |
| RISK-09 Route vocabulary mismatch | Low | Medium | Mitigated — `handoff_*` prefix for all governance fields |
| RISK-10 Offline module imports | Low | Medium | Mitigated — AST import verification in tests |
| RISK-11 Fail-open bug | Low | Critical | Mitigated — positive `handoff_allowed` assertion required |

---

## 4. Residual Risk

After all mitigations, one residual risk remains:

**RISK-08 (candidate disappearance)** has High likelihood and High impact. This is not a defect — it is the defined consequence of the architectural transition. The 208 scanner-only removals are not a failure of the handoff design; they are the handoff design. The paper mode and rollback flag de-risk the transition. Amit must acknowledge this explicitly before Sprint 7E implementation.

All other risks are mitigated by the design or covered by the test plan.

---

## 5. Go / No-Go Criteria

Implementation of Sprint 7E cannot begin until all of the following are confirmed:

| Criterion | Status |
|-----------|--------|
| Wiring point identified (`bot_trading.py:1447`) | ✅ Confirmed (Section 3, wiring design) |
| Candidate shape mapping specified | ✅ Confirmed (Section 6, wiring design) |
| Fail-closed behaviour specified (21 conditions) | ✅ Confirmed (Section 9, wiring design; Group 4, test plan) |
| Rollback path specified | ✅ Confirmed (Section 11, wiring design) |
| Implementation test plan approved | ✅ Created (`intelligence_first_controlled_handoff_implementation_test_plan.md`) |
| Metric reconciliation resolved | ✅ Confirmed (`intelligence_first_paper_current_metric_reconciliation.md`) |
| RISK-08 acknowledged by Amit | ⬜ Pending |
| **Amit explicitly approves Sprint 7E implementation** | ⬜ Pending |

---

## 6. Files This Review Covers

| File | Sprint | Status |
|------|--------|--------|
| `bot_trading.py` | Sprint 7E | Not yet modified |
| `handoff_candidate_adapter.py` | Sprint 7E | Not yet created |
| `handoff_reader.py` | Sprint 7B | Complete — no changes in 7E |
| `config.py` | Sprint 7B | Complete — flag already exists |
| `tests/test_handoff_wiring_integration.py` | Sprint 7E | Not yet created |

Do NOT touch in Sprint 7E: `scanner.py`, `signal_pipeline.py`, `signals/__init__.py`, `apex_orchestrator.py`, `guardrails.py`, `orders_core.py`, `bot_ibkr.py`.
