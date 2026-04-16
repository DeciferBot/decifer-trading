# Decifer Trading — Codebase Stability Plan
# Written: 2026-04-16. Review with Amit before executing any Tier.

---

## The Problem in One Sentence

The codebase has three compounding failure modes: **silent errors** (broken code returns nothing instead of crashing), **drifted data contracts** (JSON writers and readers use different key names), and **untested critical paths** (the files that break most often have the fewest tests).

Fix one bug without addressing these, and the next one is already hiding.

---

## Root Causes (from audit — do not re-derive)

| # | Root Cause | Blast Radius | Evidence |
|---|-----------|-------------|---------|
| 1 | **Silent failures** — bare `except: pass` swallows errors | Trade path, signals, dashboard | `signals.py` catalyst lookup returns `{}` on any error; SL order failure in `orders_core.py` is swallowed |
| 2 | **JSON without schema** — readers assume keys that writers may not write | Every data file | `catalyst_score` written by screen, read by 3 different callers with 3 different fallbacks |
| 3 | **CONFIG has no startup validation** — type/key errors surface at runtime deep in a scan cycle | Everything | `catalyst_signal_min_score` added without updating all 3 callers; mismatched defaults (7.0 vs 14.0) |
| 4 | **Multiple writers to state files, no transaction safety** | `positions.json`, `trades.json` | `orders_core.py`, `orders_portfolio.py`, `bot_ibkr.py` all write positions; no atomic writes |
| 5 | **Dashboard and file I/O have no tests** | Chief Decifer, `/api/*` routes | `bot_dashboard.py` (4371 lines, 0 tests); `trade_store.py` (0 tests); catalyst pipeline (0 end-to-end tests) |

---

## Execution Plan

### TIER 1 — Stop the Bleeding (do first, low risk, additive only)

**Goal:** make errors visible immediately instead of hiding in silent returns.

---

#### T1-A: CONFIG startup validation ✅ (shipped with this plan)

**What:** `validate_config()` added to `config.py`. Called at import time. Crashes immediately with a clear message if a required key is missing or the wrong type.

**Done criteria:** `python3 -c "import config"` with a missing key prints the offending key and exits non-zero.

**Files:** `config.py` (bottom — already done)

---

#### T1-B: Replace silent failures in the 5 critical paths

Each of these is a single-file change. Do one per session, run tests after each.

**Priority order:**

1. **`signals.py` — `_get_catalyst_lookup()`** (lines 68–82)
   - Current: `except Exception: return {}`
   - Fix: log the exception with symbol context, return `{}` but increment error counter
   - Risk: zero — same return value, just now visible
   - Test: `test_signals.py` — add one test: catalyst file missing → lookup returns `{}` AND logs warning

2. **`orders_core.py` — stop-loss order submission** (around line 430)
   - Current: `except Exception: pass` after SL order attempt
   - Fix: log exception with symbol + order details, set `sl_order_failed=True` on the trade record
   - Risk: low — additive only, no behavior change on happy path
   - Test: `test_orders_core.py` — add test: SL submit throws → position still created, failure logged

3. **`bot_ibkr.py` — fill_watcher and `_on_order_status_event`**
   - Current: status callbacks lose exceptions silently
   - Fix: wrap each callback in `try/except Exception as e: log.error(...)` with order_id context
   - Risk: low — callbacks already fire-and-forget
   - Test: `test_fill_watcher.py` — add test: callback exception → error logged, no crash

4. **`bot_dashboard.py` — all `/api/*` error handlers**
   - Current: `except Exception: pass` returns empty responses
   - Fix: return `{"error": str(e), "data": []}` so the dashboard shows "error" not "empty"
   - Risk: UI-only — dashboard shows error text instead of blank panel
   - Test: `test_dashboard.py` — add test: corrupt catalyst file → `/api/catalyst` returns `{"error": ..., "data": []}`

5. **`orders_state.py` — `_persist_positions()` failure**
   - Current: persist failure is logged but not alerted
   - Fix: on persist failure, write a `data/persist_failure.flag` file and log at ERROR level
   - Risk: zero — additive only
   - Test: `test_orders.py` — add test: persist throws → flag file created

---

#### T1-C: Standardise error logging format

All new `except` blocks in the trading path must use this pattern:

```python
except Exception as e:
    log.error("[MODULE][FUNCTION] context=%s error=%s", context_dict, e, exc_info=True)
    # then: return sentinel / increment counter / set flag
```

Not a refactor sweep — apply only to new fixes in T1-B. Existing untouched code stays as-is.

---

### TIER 2 — Data Contract Enforcement (do second, medium risk)

**Goal:** make JSON file reads fail loudly when the structure doesn't match what's expected.

**Prerequisite:** T1 complete.

---

#### T2-A: Schema validators for the 4 most-read JSON files ✅ (shipped 2026-04-16)

**What shipped:**
- `schemas.py` — 4 validators: `validate_catalyst_record`, `validate_position`, `validate_trade`, `validate_signal`
- Wired into 5 call sites: `signals/__init__.py`, `trade_store.restore()`, `ic/live.py`, `ic/data.py`, `bot_dashboard._get_catalyst_payload()`
- `tests/test_schemas.py` — 42 tests (7 helper + 7 catalyst + 10 position + 8 trade + 10 signal + 1 call-site pattern)
- Test count: 1760 passing (was 1718, +42)

**Add a `schemas.py` module (new file, ~80 lines) with one function per file:**

```python
def validate_catalyst_record(record: dict) -> None: ...
def validate_trade(trade: dict) -> None: ...
def validate_position(pos: dict) -> None: ...
def validate_signal(sig: dict) -> None: ...
```

Each function raises `ValueError("missing key X in Y")` if required keys are absent.

**Call sites to add validation:**
- `signals.py` → after reading catalyst JSON, call `validate_catalyst_record()` per record
- `orders_state.py` → after reading `positions.json`, call `validate_position()` per entry
- `trade_store.py` → after reading `trades.json`, call `validate_trade()` per entry
- `bot_dashboard.py` → after reading any JSON for `/api/*`, call the relevant validator

**Done criteria:** corrupt JSON with a missing key → the reading module logs the bad record, skips it, and continues. Not a crash, not silent success.

**Files:** new `schemas.py`, `signals.py`, `orders_state.py`, `trade_store.py`, `bot_dashboard.py`

---

#### T2-B: Sentinel return values — replace `None` with typed results ✅ (shipped 2026-04-16)

**What shipped:** Docstrings + return types updated for all 5 functions touched in T2-A.
Fixed real bug in `ic/live.py compute_live_trade_ic`: failure sentinel was missing `"timestamp"` key despite docstring promising it — now included. `_load_signal_records` return type tightened from `list` to `list[dict]`.

Functions that can fail should return a typed result, not `None`:

```python
# Before
def get_catalyst_lookup() -> dict:
    ...
    except: return {}

# After
def get_catalyst_lookup() -> dict:
    """Returns empty dict on any failure. Failure is logged."""
    ...
    except Exception as e:
        log.warning("[signals] catalyst_lookup failed: %s", e)
        return {}  # same return, now documented and logged
```

This is documentation + logging, not a type system. Apply only during T2-A edits — no sweep.

---

#### T2-C: Add schema version markers to written JSON files ✅ (shipped 2026-04-16)

**What shipped:**
- `signals/catalyst_screen.py` `run_screen()` — adds `"_schema_version": 1` to `candidates_*.json`
- `learning.py` `log_signal_scan()` — adds `"_schema_version": 1` to each `signals_log.jsonl` record
- `signals/__init__.py` + `bot_dashboard.py` — check `_schema_version` on read; warn if present and ≠ 1 (missing = old file, silently fine)
- `positions.json` and `trades.json` deferred: flat-dict-keyed and list formats can't have a top-level version key without a breaking change to all readers. Noted for Tier 3 if a wrapper format is ever adopted.

**Original description:**
When a writer creates/updates a JSON file, add `"_schema_version": 1` to the top level.

Readers check: if `_schema_version` is missing or lower than expected, log a warning and use safe defaults rather than crashing.

This gives future-you a way to detect stale files without guessing.

**Files:** `signals/catalyst_screen.py` (writer), `trade_store.py` (writer/reader), `orders_state.py` (writer/reader)

---

### TIER 3 — Regression Safety Net ✅ COMPLETE (session 2026-04-16)

**Goal:** make it impossible to break the most-broken things without a test catching it.

1795 tests passing (was 1760 at T2 completion, +35).

| File | Tests | What it covers |
|------|-------|---------------|
| `tests/test_trade_store.py` | 14 | `persist` (write, RESERVED filter, instrument filter, overwrite, empty), `restore` (missing, corrupt, valid, bad-record skip+WARNING, roundtrip), `ledger_write`/`ledger_lookup` (roundtrip, first-write-wins, missing key, UNKNOWN trade_type) |
| `tests/test_catalyst_pipeline.py` | 8 | Bad record skip + WARNING (missing ticker, missing score, all bad), schema version (v1 silent, unknown warns+processes, missing silent), cache hit, cache reset |
| `tests/test_positions_persistence.py` | 13 | `_save_positions_file` (create, roundtrip, RESERVED excluded, missing→`{}`, corrupt→`{}`, non-dict→`{}`, parent dir created), `_is_recently_closed` (absent, within cooldown, past cooldown), `cleanup_recently_closed` (evicts stale, keeps fresh, empty) |

---

## What NOT to Do

| Temptation | Why not |
|-----------|---------|
| Refactor all `except` blocks in one PR | Touches too many files, hard to review, high regression risk |
| Add type hints everywhere | No behavior change, costs review time |
| Centralize all JSON I/O into one module | Tier 3 refactor — requires design session with Amit first |
| Run Tier 2 before Tier 1 | You can't enforce schemas if errors are still silent |
| Add new features during stabilization | Every new feature adds another silent failure point |

---

## Session Protocol for Stability Work

Each stability session should:
1. Pick **one item** from the current Tier
2. Read the target file before touching it
3. Make the change
4. Run `pytest tests/` — verify pass count does not drop
5. Add the test(s) listed in the item
6. Run `pytest` again — new tests must pass
7. Commit with `fix(stability): <item name>`

Do not combine multiple items in one session. Small, reviewable diffs.

---

## Progress Tracker

| Item | Status | Session |
|------|--------|---------|
| T1-A: CONFIG validation | ✅ Done | 2026-04-16 |
| T1-B-1: signals.py catalyst lookup | ⬜ | — |
| T1-B-2: orders_core.py SL submission | ⬜ | — |
| T1-B-3: bot_ibkr.py fill_watcher | ⬜ | — |
| T1-B-4: bot_dashboard.py API handlers | ⬜ | — |
| T1-B-5: orders_state.py persist failure | ⬜ | — |
| T1-C: Logging format standard | ⬜ | — |
| T2-A: Schema validators | ⬜ | — |
| T2-B: Sentinel return values | ⬜ | — |
| T2-C: Schema version markers | ⬜ | — |
| T3-1: test_dashboard.py hardening | ⬜ | — |
| T3-2: test_trade_store.py (new) | ⬜ | — |
| T3-3: test_catalyst_pipeline.py (new) | ⬜ | — |
| T3-4: test_positions_persistence.py (new) | ⬜ | — |
