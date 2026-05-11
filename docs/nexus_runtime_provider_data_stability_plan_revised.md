# Nexus Runtime / Provider / Data Stability Sprint — Implementation Record

**Sprint:** `fix/nexus-runtime-provider-data-stability`  
**Branch:** `claude/dreamy-chaum-838852`  
**Completed:** 2026-05-11  

---

## What Was Fixed

### Fix 1: FMP Negative Caching (`fmp_client.py`)

**Root cause:** `_cache` was only written on successful responses. HTTP 402 and `{"Error Message": ...}` responses returned `None` uncached — every subsequent scan cycle would repeat the failing network call. With `warm_fundamentals_cache()` called for ~100 symbols × 3 endpoints per cycle, this caused 300–2000 wasted network calls/hour.

**Fix:** Added `_neg_cache: dict[str, float]` (key → blocked_until epoch). HTTP 402 (account entitlement, won't resolve until plan upgrade) suppressed for 24h. `{"Error Message": ...}` (quota-based, may reset daily) suppressed for 4h. HTTP 5xx not cached (transient). The 402 log message now says "account entitlement" rather than the generic HTTP status.

**Account action required:** The 5 failing endpoints (`income-statement-growth`, `income-statement`, `earnings`, `key-metrics-ttm`, `financial-scores`) require a higher FMP plan. Code fix prevents repeated calls regardless.

---

### Fix 2: AV Multi-Ticker Guard + Error Message Handling (`alpha_vantage_client.py`)

**Root cause:** AV NEWS_SENTIMENT `tickers=` is a **simultaneous-mention filter** — articles returned must mention ALL listed tickers simultaneously. Passing 50 unrelated tickers returns near-zero articles. The current `get_news_sentiment(to_fetch)` call from `news.py` passes the entire uncached symbol batch (1–100 symbols) — the enrichment was producing silent garbage data.

Additionally, AV returns `{"Error Message": "..."}` for plan-level restrictions, but all three parsers only checked for `"Note"` and `"Information"` keys — Error Message responses fell through to empty feed parsing with no warning logged.

**Fix:** 
- `get_news_sentiment()`: guard at top — `if len(tickers) > 1: return {}` with log.info. Single-symbol calls proceed normally. Returns `{}` cleanly; caller uses RSS-only fallback.
- All three parsers (`get_news_sentiment`, `get_news_articles`, `get_sector_performance`): added `"Error Message"` to the existing Note/Information check. Provider errors now log a warning and return gracefully.

**Deferred:** Per-symbol top-N redesign (single-symbol calls, quota-controlled, per-symbol cache keys) is the correct long-term fix. Deferred to a separate sprint requiring separate Amit approval.

---

### Fix 3: Alpaca Handoff-First Startup (`bot.py`)

**Root cause:** The startup bar stream block unconditionally called `get_dynamic_universe()` (scanner, ~233 symbols). When Nexus handoff is active, startup should seed BAR_CACHE with handoff symbols so the first scan doesn't cold-start.

**Fix:** When `enable_active_opportunity_universe_handoff` is True, attempt `load_production_handoff()` first. Graceful fallback to scanner on any failure. Logs `source=handoff_reader` or `source=scanner` for observability.

**BAR_CACHE note:** `stop()` does NOT clear BAR_CACHE — old scanner bars persist until evicted by the 1200-bar limit, but are inert since the scan cycle only processes handoff symbols. This fix ensures handoff symbols have bars from startup.

---

### Fix 4: thesis_store Step 4 in Intelligence Pipeline (`run_intelligence_pipeline.py`)

**Root cause:** `handoff_publisher.py` reads `thesis_store.json` as optional metadata. `generate_thesis_store()` was not called from the pipeline, so `thesis_store.json` was never written.

**Fix:** Added `from thesis_store import generate_thesis_store` and Step 4 in `run()`. Step 4 runs after Step 3 (theme_activation) — all inputs available. Handles missing files gracefully via `unavailable_sources`.

---

### Fix 5: Evidence Baseline Script (`scripts/capture_nexus_baseline.py`)

Read-only evidence capture. No broker, LLM, or provider calls. Captures config flags, file mtimes, manifest freshness, provider cache state, and bug classification table. Output: `data/runtime/nexus_runtime_bug_baseline.json` (gitignored — runtime data).

---

## Deferred / No-Action Items

| # | Issue | Decision |
|---|-------|----------|
| 5 | PRU cache stale | No action — rescue is gated; no live scoring impact |
| 6 | `current_manifest.json` absent in worktree | Env issue — not a code bug |
| 7 | IBIT OCA 10327 | Position protected by existing handler |
| 8 | `USE_APEX_V3_SHADOW = True` | Correct — shadow logging active |
| 9 | `symbol_master.json` / `layer_factor_map.json` 5d old | Reference data; weekly rebuild is normal |
| 11 | Handoff gate tests fail in worktree | Env issue — `data/live/*.json` absent |
| AV per-symbol redesign | Deferred to separate sprint | Requires Amit approval of top-N design |
| FMP endpoint retirement | Operational decision for Amit | Code fix prevents repeated calls regardless |

---

## Tests Added

| File | Tests |
|------|-------|
| `tests/test_fmp_negative_cache.py` | 7 (402 neg cache, Error Message neg cache, TTL, expiry, log, 5xx guard) |
| `tests/test_av_multi_ticker_guard.py` | 6 (multi-ticker guard, single-ticker path, Error Message in articles) |
| `tests/test_alpaca_startup_source.py` | 6 (handoff used, fallback on error, not allowed, empty candidates, dedup, disabled) |
| `tests/test_intelligence_pipeline_thesis_store.py` | 3 (import, call order, no-crash on missing inputs) |

Regression suites:
- `tests/test_alpha_vantage_client.py`: 15/15 pass
- `tests/test_handoff_wiring_integration.py`: 97 pass, 3 skip (unchanged)

---

## Runtime Smoke Validation

```bash
# Fix 1 — FMP 402 suppressed (appears once, then stops)
grep "account entitlement" /tmp/decifer.log | head -5

# Fix 2 — AV multi-ticker guard active
grep "AV news sentiment skipped" /tmp/decifer.log | head -3

# Fix 3 — Alpaca startup source
grep "bar stream active" /tmp/decifer.log | head -1
# → "source=handoff_reader" when manifest present, "source=scanner" otherwise

# Fix 4 — thesis_store written
python3 run_intelligence_pipeline.py
# → "[4/4] Building thesis store..."
```
