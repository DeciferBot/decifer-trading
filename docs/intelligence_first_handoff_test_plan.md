# Intelligence-First Production Handoff — Test Plan

**Sprint:** 7A
**Status:** DESIGN ONLY — tests not yet implemented
**Created:** 2026-05-06
**Purpose:** Define the full test suite required before any handoff implementation is approved.

These tests must all pass before `enable_active_opportunity_universe_handoff` is set to `True` in any environment. No implementation of the handoff reader should begin until Sprint 7B is approved.

---

## Test Group 1: File Availability Tests

**Purpose:** Verify that the handoff reader correctly detects the presence or absence of `active_opportunity_universe.json` and logs the result.

**Expected behaviour:** Reader logs `HANDOFF_READ_ATTEMPT` on each call. If file present, proceeds to schema validation. If file absent, logs `HANDOFF_FILE_MISSING` and triggers fail-closed.

**Failure mode:** Silently continuing with empty candidates when file is absent.

**Required assertions:**
- `assert` reader returns zero candidates when file is missing
- `assert` structured log entry `HANDOFF_FILE_MISSING` emitted
- `assert` no fallback to scanner, LLM, or raw news
- `assert` PM path (Track B) is not affected

**Production risk mitigated:** Bot entering zero-candidate scan cycle silently; or worse, falling back to scanner without logging the trigger.

---

## Test Group 2: Schema Validation Tests

**Purpose:** Verify that the handoff reader correctly validates the top-level schema of `active_opportunity_universe.json`.

**Expected behaviour:** Required top-level fields (`schema_version`, `generated_at`, `candidates`, `safety_flags`) are present and correctly typed. Missing or wrong-type fields trigger fail-closed.

**Failure mode:** Partial schema accepted; malformed file produces untested candidate list.

**Required assertions:**
- `assert` reader rejects file with missing `candidates` field
- `assert` reader rejects file with `candidates` not a list
- `assert` reader rejects file with `schema_version` absent
- `assert` reader accepts file with all required fields present and valid
- `assert` reader logs `HANDOFF_SCHEMA_INVALID` on rejection

**Production risk mitigated:** Corrupted or truncated file passing validation and producing partial or wrong candidate list.

---

## Test Group 3: Freshness Tests

**Purpose:** Verify that the handoff reader rejects stale files based on `generated_at` timestamp and configured `max_age_minutes`.

**Expected behaviour:** If `generated_at` is older than `max_age_minutes` (to be configured in Sprint 7B), reader logs `HANDOFF_FILE_STALE` and returns zero candidates.

**Failure mode:** Stale file from previous session used as if fresh; old universe fed to Apex.

**Required assertions:**
- `assert` reader rejects file with `generated_at` older than threshold
- `assert` reader accepts file with `generated_at` within threshold
- `assert` structured log entry `HANDOFF_FILE_STALE` emitted on rejection
- `assert` fail-closed behaviour on stale — no candidates passed to scoring

**Production risk mitigated:** Universe Builder offline for several hours; last-known-good file silently consumed and fed to Apex with stale candidates.

---

## Test Group 4: Candidate Field Tests

**Purpose:** Verify that individual candidate records are validated field-by-field and that candidates missing required fields are rejected (not silently dropped or accepted with defaults).

**Expected behaviour:** Each candidate must have `symbol`, `reason_to_care`, `route`, `source_labels`. Any candidate missing any of these is rejected individually and logged.

**Failure mode:** Candidates with empty `reason_to_care` or missing `route` silently admitted; scoring proceeds on unlabelled symbols.

**Required assertions:**
- `assert` candidate missing `symbol` is rejected, logged `CANDIDATE_MISSING_SYMBOL`
- `assert` candidate missing `reason_to_care` is rejected, logged `CANDIDATE_MISSING_REASON_TO_CARE`
- `assert` candidate missing `route` is rejected, logged `CANDIDATE_MISSING_ROUTE`
- `assert` candidate missing `source_labels` is rejected, logged `CANDIDATE_MISSING_SOURCE_LABELS`
- `assert` valid candidate passes all field checks and is admitted
- `assert` partial file (some valid, some invalid candidates) returns only valid candidates

**Production risk mitigated:** Unlabelled symbols entering scoring pool; Apex evaluating symbols with no intelligence context.

---

## Test Group 5: Fail-Closed Tests

**Purpose:** Verify that all fail-closed triggers produce zero new-entry candidates and the correct log entry, with no fallback to any alternative discovery source.

**Expected behaviour:** On any fail-closed trigger, `candidates_for_scoring = []` for new entries. PM review of existing positions continues (Track B is not affected).

**Failure mode:** Any path where a fail-closed trigger produces non-empty candidate list or falls back silently.

**Required assertions:**
- `assert` fail-closed on missing file → `candidates = []`
- `assert` fail-closed on stale file → `candidates = []`
- `assert` fail-closed on schema invalid → `candidates = []`
- `assert` fail-closed on `executable=true` flag → `candidates = []` + log `EXECUTABLE_FLAG_VIOLATION`
- `assert` fail-closed on zero valid candidates after per-field validation → log `HANDOFF_ZERO_VALID_CANDIDATES`
- `assert` Track B PM path receives existing positions normally in all fail-closed cases
- `assert` log entry emitted for every fail-closed path

**Production risk mitigated:** Silent partial failure where bad candidates pass through; or PM review being blocked by handoff reader failure.

---

## Test Group 6: No Fallback Discovery Tests

**Purpose:** Verify that no code path inside the handoff reader or the bot-trading integration calls `scanner.get_dynamic_universe()` as a fallback.

**Expected behaviour:** When handoff is enabled and the file fails any check, the bot operates with zero new-entry candidates. It does not call the scanner.

**Failure mode:** Silent fallback to scanner when handoff file is unavailable; production appears unchanged but is running the old path.

**Required assertions:**
- `assert scanner.get_dynamic_universe not called` when `enable_active_opportunity_universe_handoff=True`
- `assert scanner.get_dynamic_universe called` when `enable_active_opportunity_universe_handoff=False` (regression test)
- Mock scanner and assert it is never invoked in handoff-enabled path

**Production risk mitigated:** Undetected fallback; entire handoff design rendered ineffective because old scanner path silently restored on every file miss.

---

## Test Group 7: No LLM Discovery Tests

**Purpose:** Verify that the handoff reader never calls an LLM to discover or supplement candidates.

**Expected behaviour:** Zero LLM calls in the handoff reader's code path. The handoff file is the only symbol source.

**Failure mode:** LLM called as a supplementation mechanism when handoff file is sparse or missing.

**Required assertions:**
- `assert` no `anthropic` import in handoff reader module
- AST check: handoff reader has no calls to `anthropic.Anthropic()`, `apex_call()`, or `market_intelligence`
- `assert llm_called = false` in all handoff reader logs

**Production risk mitigated:** LLM used as a fallback discovery engine; uncontrolled symbol admission bypassing the approved universe.

---

## Test Group 8: No Raw News Tests

**Purpose:** Verify that the handoff reader does not use raw news feeds, RSS scraping, or news-triggered symbol discovery.

**Expected behaviour:** No network calls to news APIs or scraping targets from within the handoff reader.

**Failure mode:** News-triggered candidates admitted through handoff path, bypassing thematic and economic filter.

**Required assertions:**
- AST check: no imports of news-adjacent modules (`overnight_research`, `catalyst_engine`, `sentinel_agents`, RSS libraries) in handoff reader
- `assert raw_news_used = false` in handoff reader logs
- `assert` no HTTP requests initiated by handoff reader (mock `requests` / `httpx` and assert not called)

**Production risk mitigated:** Raw news as an emergency symbol source; uncontrolled admission of news-driven symbols.

---

## Test Group 9: No Broad Scan Tests

**Purpose:** Verify that the handoff reader does not trigger `score_universe()` on a scan of the full committed universe or any broad symbol set.

**Expected behaviour:** `score_universe()` is called only with candidates from the validated handoff file, not with a broad universe.

**Failure mode:** Handoff reader silently falls back to scoring the full committed universe when the handoff file is insufficient.

**Required assertions:**
- `assert` `score_universe()` receives only the handoff-validated candidate list
- `assert` candidate count passed to `score_universe()` equals validated handoff candidate count
- `assert broad_intraday_scan_used = false` in handoff reader logs

**Production risk mitigated:** Full universe scan disguised as a handoff; advisory-based filtering bypassed by broad scoring.

---

## Test Group 10: Manual/Held Protection Tests

**Purpose:** Verify that manual conviction and held position protections are preserved end-to-end after handoff implementation.

**Expected behaviour:** All 13 manual conviction symbols enter the scoring candidate pool regardless of which Universe Builder path assigned them. Held positions are protected by existing `orders_state.py` logic, not by the handoff file.

**Failure mode:** Manual conviction symbols excluded because they fail some new source-label requirement; or held positions incorrectly unprotected.

**Required assertions:**
- `assert` all `favourites.json` symbols appear in candidate list when Universe Builder assigns them `source_label=favourites_manual_conviction`
- `assert` held positions are protected by `orders_state` logic independent of handoff file content
- `assert` handoff file with manual conviction symbols passes field validation
- `assert` removing a symbol from handoff file does not affect its held protection if it has an open position

**Production risk mitigated:** Manual conviction symbols dropped from scoring because they lack a required handoff field; or held position protection accidentally routed through intelligence layer.

---

## Test Group 11: Tier D Source Label Tests

**Purpose:** Verify that Tier D (position research) symbols are admitted via the `position_research_universe` source label, and that Tier D membership alone (without `reason_to_care`) does not grant admission.

**Expected behaviour:** A symbol with `source_labels=["position_research_universe"]` and a valid `reason_to_care` is admitted. A symbol with Tier D membership but no `reason_to_care` in the handoff file is not admitted.

**Failure mode:** All 150 Tier D symbols admitted without individual `reason_to_care` validation, bypassing the quota constraint.

**Required assertions:**
- `assert` Tier D symbol with valid `reason_to_care` and `source_labels` is admitted
- `assert` Tier D symbol without `reason_to_care` is rejected with log `CANDIDATE_MISSING_REASON_TO_CARE`
- `assert` structural quota (20 slots) is enforced by `quota_allocator` even after handoff
- `assert` Tier D source label alone does not override quota constraint

**Production risk mitigated:** All 150 Tier D symbols flooding scoring pool without quota constraint, replicating the pre-quota-allocator flat-pool problem.

---

## Test Group 12: Route Integrity Tests

**Purpose:** Verify that routes assigned by the Universe Builder are preserved through the handoff reader and passed unchanged to scoring; and that `intraday_swing`/`swing` normalisation is applied correctly.

**Expected behaviour:** `route` field is passed as-is from the handoff file to the candidate struct. Route normalisation (if implemented) is explicit and logged. No route rewriting by Apex.

**Failure mode:** Routes silently rewritten or lost during handoff; `intraday_swing → watchlist` disagreements not respected.

**Required assertions:**
- `assert` candidate `route` in scoring input matches `route` in handoff file (or normalised equivalent)
- `assert` normalisation table (if implemented) is explicit and tested in isolation
- `assert` `intraday_swing → watchlist` disagreement symbols are not routed to Apex execution track
- `assert` `route_disagreement` log emitted when mismatch detected

**Production risk mitigated:** Symbols downgraded to `watchlist` in the intelligence layer being promoted back to execution tier silently.

---

## Test Group 13: Apex Bounded-Input Tests

**Purpose:** Verify that Apex receives only the curated candidate list from the handoff reader, with no additional symbols added by Apex itself.

**Expected behaviour:** The candidate list passed to `apex_call()` matches exactly the validated and routed list from the handoff reader. Apex cannot add to or expand the list.

**Failure mode:** Apex receiving an un-curated list; or Apex discovering symbols outside the handoff file.

**Required assertions:**
- `assert` candidates passed to `apex_call()` are a subset of (or equal to) handoff-validated candidates
- `assert` no symbol appears in `apex_call()` input that was not in the handoff file
- `assert` Apex output contains no symbol not in its input (no hallucinated new entries)
- `assert` `apex_input_changed = false` in all handoff reader records

**Production risk mitigated:** Apex expanding the candidate list via LLM reasoning; uncontrolled symbol admission through model inference.

---

## Test Group 14: Risk/Order/Execution No-Change Tests

**Purpose:** Verify that production handoff changes only the candidate source. All risk gates, position sizing, order logic, and execution remain identical.

**Expected behaviour:** `risk.py`, `orders_core.py`, `guardrails.py`, `orders_options.py` are unmodified and their test suites produce identical results before and after handoff implementation.

**Failure mode:** Risk thresholds, position sizing, or order gates silently changed as a side effect of handoff wiring.

**Required assertions:**
- `assert` test_risk.py passes identically before and after handoff implementation
- `assert` test_orders_core.py smoke passes
- `assert` test_orders_execute.py passes
- `assert` guardrails test suite passes
- `assert` no new imports from handoff reader into risk/orders/guardrails modules
- `assert` no parameter changes to `score_universe()`, `execute_buy()`, `execute_short()`, `execute_buy_option()`

**Production risk mitigated:** Risk parameters changed by side effect; position sizing altered by new candidate metadata fields.

---

## Test Group 15: Rollback Flag Tests

**Purpose:** Verify that setting `enable_active_opportunity_universe_handoff = False` fully restores pre-handoff behaviour without any code change.

**Expected behaviour:** When flag is False, bot calls `scanner.get_dynamic_universe()` as before. Handoff reader is not called. `active_opportunity_universe.json` is not read. No advisory or handoff logs emitted on the execution path.

**Failure mode:** Flag off does not fully restore scanner path; some handoff logic runs even when flag is False.

**Required assertions:**
- `assert` scanner called when flag=False
- `assert` scanner not called when flag=True
- `assert` no `HANDOFF_*` log events when flag=False
- `assert` switching flag from True to False and back produces identical candidate sets (no state contamination)
- `assert` rollback does not require restart of bot (hot flag read)

**Production risk mitigated:** Rollback requiring code change or restart; state contamination between flag-on and flag-off modes.

---

## Test Group 16: Observability Tests

**Purpose:** Verify that all required log events are emitted with correct structure for log aggregator compatibility.

**Expected behaviour:** Every handoff reader outcome produces at least one structured log entry with `key=value` or JSON format.

**Failure mode:** Silent failures; or unstructured log messages that cannot be parsed by a log aggregator.

**Required assertions:**
- `assert` each log event from the required log table is emitted in at least one test scenario
- `assert` log entries contain `timestamp`, `event_key`, and `reason` fields
- `assert` `live_output_changed=false` appears in all handoff reader diagnostic logs
- `assert` `HANDOFF_FAIL_CLOSED` is emitted for every fail-closed trigger

**Production risk mitigated:** Silent failures in production; on-call engineer unable to determine why bot is in zero-new-entry mode.

---

## Test Group 17: Production Simplification Tests

**Purpose:** Verify that the handoff implementation adds no duplicate logic, no new live-data paths, no new broker calls, and no new zombie modules.

**Expected behaviour:** The handoff reader is a single new module (`production_runtime`). No existing module is duplicated. No new LLM, API, or broker path is introduced.

**Failure mode:** Handoff implementation introduces a second candidate-scoring path, a second regime detector, or a duplicate quota allocator.

**Required assertions:**
- `assert` only one new module added by Sprint 7B implementation
- `assert` handoff reader has zero imports of: `scanner.py`, `market_intelligence.py`, `bot_trading.py`, `orders_core.py`, `guardrails.py`, `catalyst_engine.py`, `overnight_research.py`
- `assert` no new calls to `score_universe()` are introduced (handoff reader feeds into the existing one call)
- `assert` no duplicate `quota_allocator.py` or `route_tagger.py` logic in handoff reader
- Classification check: every new file is classified in the production simplification audit before Sprint 7B is closed

**Production risk mitigated:** Architecture becoming "a second bot beside the old bot" — two parallel scoring/risk/execution paths with no clear authority boundary.
