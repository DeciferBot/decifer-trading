# Intelligence-First Runtime Failure Modes

**Created:** 2026-05-07
**Sprint:** 7A.4
**Status:** Design / Pre-production
**Owner:** Cowork (Claude)
**Approver:** Amit

This document enumerates every significant failure mode in the Intelligence-First production runtime, with detection, expected behaviour, and alerting policy.

---

## Failure Mode Reference

### 1. Reference data worker fails

| Field | Value |
|-------|-------|
| **Detection** | `data/heartbeats/reference_data_worker.json` missing or `last_success_at` > 8 days; or reference file `generated_at` > 8 days |
| **Expected behaviour** | Worker logs structured error; retries 3×; does not overwrite existing valid reference files |
| **Fail-closed response** | Intelligence pipeline continues with prior reference data until expired (>14 days) |
| **Log message** | `reference_data_worker: FAIL — {error}; retaining prior reference files` |
| **Alert required** | Yes, if > 14 days stale |
| **Production risk** | Low short-term; symbol master and sector schema drift over weeks |
| **Test required later** | Yes — integration test: worker fails, prior files retained, validator still passes |

---

### 2. Provider ingestion worker fails

| Field | Value |
|-------|-------|
| **Detection** | `data/heartbeats/provider_ingestion_worker.json` missing or `last_success_at` > 15 min; no fresh provider snapshot |
| **Expected behaviour** | Worker logs error; retries 2×; does not publish partial snapshot |
| **Fail-closed response** | Technical sensor worker skips cycle if no valid provider snapshot within 20 min |
| **Log message** | `provider_ingestion_worker: FAIL — no provider snapshot published` |
| **Alert required** | Yes, if > 30 min during market hours |
| **Production risk** | Medium — technical scoring degrades; universe stalens |
| **Test required later** | Yes |

---

### 3. Provider API rate limit hit

| Field | Value |
|-------|-------|
| **Detection** | HTTP 429 response; provider rate limit error in worker log |
| **Expected behaviour** | Worker backs off per provider rate limit guidance; retries after backoff; logs `rate_limit_hit` with provider name |
| **Fail-closed response** | Partial snapshot written with `data_completeness < 1.0`; downstream workers note reduced coverage |
| **Log message** | `provider_ingestion_worker: RATE_LIMIT — provider={provider}, retry_after={s}s` |
| **Alert required** | Yes, if persistent (> 3 consecutive cycles) |
| **Production risk** | Medium — Alpha Vantage free tier: 25 req/day. Alpaca: 200 req/min. FMP: 750 req/min |
| **Test required later** | Yes — mock rate limit response; verify backoff |

---

### 4. Provider credentials missing

| Field | Value |
|-------|-------|
| **Detection** | Worker calls `os.getenv(KEY)` and receives empty string; logs `credentials_missing` |
| **Expected behaviour** | Worker skips the provider; logs structured error; continues with other providers if available |
| **Fail-closed response** | Affected provider's factors marked `unavailable` in snapshot; downstream notes reduced coverage |
| **Log message** | `provider_ingestion_worker: CREDENTIALS_MISSING — provider={provider}; skipping` |
| **Alert required** | Yes — missing credentials is a configuration error |
| **Production risk** | High — Alpaca credentials missing = no market data |
| **Test required later** | Yes |

---

### 5. Provider returns partial data

| Field | Value |
|-------|-------|
| **Detection** | Response contains fewer symbols than expected; `data_completeness < threshold` in snapshot |
| **Expected behaviour** | Worker writes snapshot with `data_completeness` flag; logs coverage gap |
| **Fail-closed response** | Universe Builder notes reduced coverage; outputs snapshot with `freshness_status = stale_fallback` for affected symbols |
| **Log message** | `provider_ingestion_worker: PARTIAL_DATA — expected={n}, received={m}, completeness={pct}` |
| **Alert required** | Yes, if < 80% completeness |
| **Production risk** | Medium |
| **Test required later** | Yes |

---

### 6. EIL (Economic Intelligence) worker fails

| Field | Value |
|-------|-------|
| **Detection** | `data/heartbeats/economic_intelligence_worker.json` missing or `current_economic_context.json` > 26 hours |
| **Expected behaviour** | Worker retries 3×; on failure retains prior economic context with `freshness_status = stale_fallback` |
| **Fail-closed response** | Universe Builder uses prior context if within 48-hour stale window; fails closed beyond 48 hours |
| **Log message** | `economic_intelligence_worker: FAIL — retaining prior context from {generated_at}` |
| **Alert required** | Yes, if > 26 hours |
| **Production risk** | Medium — theme activations may drift from market reality |
| **Test required later** | Yes |

---

### 7. Catalyst worker fails

| Field | Value |
|-------|-------|
| **Detection** | No fresh catalyst snapshot within 8 hours during market hours |
| **Expected behaviour** | Worker retries; on failure uses prior snapshot with staleness flag |
| **Fail-closed response** | Universe Builder uses prior catalyst snapshot if within 8-hour stale window; excludes catalyst-driven candidates beyond 12 hours |
| **Log message** | `catalyst_event_worker: FAIL — no fresh catalyst snapshot; using prior from {generated_at}` |
| **Alert required** | Yes, if > 8 hours during market hours |
| **Production risk** | Medium — earnings/catalyst candidates may be missed or stale |
| **Test required later** | Yes |

---

### 8. Technical sensor worker fails

| Field | Value |
|-------|-------|
| **Detection** | No fresh technical snapshot within 15 min during market hours |
| **Expected behaviour** | Worker retries 1×; logs failure; does not publish partial snapshot |
| **Fail-closed response** | Universe Builder skips cycle; prior universe snapshot remains active if within stale window |
| **Log message** | `technical_market_sensor_worker: FAIL — no technical snapshot published` |
| **Alert required** | Yes, if > 20 min during market hours |
| **Production risk** | High — scoring degrades to prior snapshot |
| **Test required later** | Yes |

---

### 9. Universe Builder fails

| Field | Value |
|-------|-------|
| **Detection** | No fresh active universe snapshot within 15 min; or snapshot `validation_status = fail` |
| **Expected behaviour** | Builder logs detailed validation errors; does not publish a failed snapshot to staging |
| **Fail-closed response** | Handoff Publisher retains prior valid manifest; live bot continues on prior universe if within stale window |
| **Log message** | `universe_builder_worker: FAIL — validation_errors={errors}` |
| **Alert required** | Yes, if > 20 min during market hours |
| **Production risk** | High — new entries may be blocked; positions not affected |
| **Test required later** | Yes |

---

### 10. Handoff validator fails

| Field | Value |
|-------|-------|
| **Detection** | `data/live/manifest_fail_{ts}.json` present; `current_manifest.json` not updated |
| **Expected behaviour** | Publisher logs all failing invariants; does not update manifest; live bot continues on prior manifest |
| **Fail-closed response** | Live bot reads prior manifest until it expires; then degrades gracefully |
| **Log message** | `handoff_validator_publisher: FAIL_CLOSED — reason={fail_closed_reason}` |
| **Alert required** | Yes |
| **Production risk** | Medium — new intelligence not published; live bot on stale universe |
| **Test required later** | Yes — each invariant breach tested |

---

### 11. Manifest stale

| Field | Value |
|-------|-------|
| **Detection** | Live bot reads `current_manifest.json` and `expires_at` < now |
| **Expected behaviour** | Bot logs `manifest_expired`; stops entering new positions; holds open positions |
| **Fail-closed response** | No new entries when handoff is enabled; existing positions managed normally |
| **Log message** | `live_trading_bot: MANIFEST_EXPIRED — expires_at={ts}; holding positions only` |
| **Alert required** | Yes |
| **Production risk** | Medium — new signal blocked; positions held appropriately |
| **Test required later** | Yes |

---

### 12. Active universe file missing

| Field | Value |
|-------|-------|
| **Detection** | Manifest references `active_universe_file` but file does not exist at that path |
| **Expected behaviour** | Bot logs `active_universe_missing`; treats as manifest invalid |
| **Fail-closed response** | No new entries; existing positions held |
| **Log message** | `live_trading_bot: ACTIVE_UNIVERSE_MISSING — path={path}` |
| **Alert required** | Yes |
| **Production risk** | Medium |
| **Test required later** | Yes |

---

### 13. Active universe schema invalid

| Field | Value |
|-------|-------|
| **Detection** | Bot reads active universe file and schema validation fails |
| **Expected behaviour** | Bot logs schema errors; treats as manifest invalid |
| **Fail-closed response** | No new entries; existing positions held |
| **Log message** | `live_trading_bot: ACTIVE_UNIVERSE_SCHEMA_FAIL — errors={errors}` |
| **Alert required** | Yes |
| **Production risk** | Medium |
| **Test required later** | Yes |

---

### 14. Candidate missing required field

| Field | Value |
|-------|-------|
| **Detection** | Candidate in active universe missing `symbol`, `score`, `route_bias`, or `source_label` |
| **Expected behaviour** | Universe Builder rejects the candidate at build time; logs missing field |
| **Fail-closed response** | Candidate excluded from published universe |
| **Log message** | `universe_builder_worker: CANDIDATE_MISSING_FIELD — symbol={sym}, field={field}` |
| **Alert required** | No (expected for some symbols) |
| **Production risk** | Low — individual candidate excluded |
| **Test required later** | Yes |

---

### 15. Candidate contains `executable = true`

| Field | Value |
|-------|-------|
| **Detection** | Snapshot validator finds `candidate["executable"] = true` |
| **Expected behaviour** | Handoff Publisher rejects entire snapshot; writes `manifest_fail` with `executable_trade_instruction_detected` |
| **Fail-closed response** | Manifest not updated; critical invariant breach logged and alerted |
| **Log message** | `handoff_validator_publisher: CRITICAL — executable_trade_instruction_detected; symbol={sym}` |
| **Alert required** | Yes — **immediate** |
| **Production risk** | Critical — this should never happen; represents a design violation |
| **Test required later** | Yes — negative test: inject executable=true; verify rejection |

---

### 16. Candidate source not approved

| Field | Value |
|-------|-------|
| **Detection** | Candidate `source_label` not in approved source list |
| **Expected behaviour** | Universe Builder logs warning; candidate may be excluded or flagged |
| **Fail-closed response** | Handoff Publisher rejects snapshot if unapproved source count exceeds threshold |
| **Log message** | `universe_builder_worker: UNAPPROVED_SOURCE — symbol={sym}, source_label={label}` |
| **Alert required** | Yes if > 5% of candidates are from unapproved sources |
| **Production risk** | Medium — data quality concern |
| **Test required later** | Yes |

---

### 17. Live bot cannot read manifest

| Field | Value |
|-------|-------|
| **Detection** | `open(manifest_path)` raises `IOError` or `PermissionError` |
| **Expected behaviour** | Bot logs `manifest_read_error`; treats as manifest missing |
| **Fail-closed response** | No new entries; existing positions held |
| **Log message** | `live_trading_bot: MANIFEST_READ_ERROR — {error}` |
| **Alert required** | Yes |
| **Production risk** | High — filesystem or permission issue |
| **Test required later** | Yes |

---

### 18. IBKR market data unavailable

| Field | Value |
|-------|-------|
| **Detection** | IBKR historical data request returns error 10091 (no data) or times out |
| **Expected behaviour** | Bot logs `ibkr_market_data_unavailable`; falls back to Alpaca for price data |
| **Fail-closed response** | Scoring proceeds with Alpaca data; IBKR market data gap logged |
| **Log message** | `bot_ibkr: IBKR_MARKET_DATA_UNAVAILABLE — error=10091; falling back to Alpaca` |
| **Alert required** | No (known IBKR limitation; handled by fallback) |
| **Production risk** | Low — Alpaca is primary data source |
| **Test required later** | No — existing handling covers this |

---

### 19. IBKR execution gateway unavailable

| Field | Value |
|-------|-------|
| **Detection** | IBKR connection drops; `ib.isConnected()` returns False |
| **Expected behaviour** | Bot attempts reconnect with exponential backoff; halts order submission during disconnect |
| **Fail-closed response** | No new orders submitted; in-flight orders tracked; positions held |
| **Log message** | `bot_ibkr: IBKR_DISCONNECTED — halting order submission; reconnect attempt {n}` |
| **Alert required** | Yes, if > 5 minutes |
| **Production risk** | High — no execution during disconnect |
| **Test required later** | Yes — mock disconnect |

---

### 20. Advisory logger fails

| Field | Value |
|-------|-------|
| **Detection** | `advisory_logger.log_advisory_context()` raises exception (caught internally) |
| **Expected behaviour** | Exception logged as DEBUG; scan cycle continues unaffected |
| **Fail-closed response** | Advisory logging is non-critical; production not affected |
| **Log message** | `advisory_logger: NON_CRITICAL_EXCEPTION — {error}` |
| **Alert required** | No |
| **Production risk** | None — advisory is observational only |
| **Test required later** | No |

---

### 21. Log volume grows too large

| Field | Value |
|-------|-------|
| **Detection** | `advisory_runtime_log.jsonl` > 100MB; or `logs/` directory > 10GB |
| **Expected behaviour** | Observability worker triggers rotation; compresses old log |
| **Fail-closed response** | If rotation fails: stop appending to advisory log; production unaffected |
| **Log message** | `observability_worker: LOG_ROTATION — file={path}, size={mb}MB` |
| **Alert required** | Yes, if > 500MB without rotation |
| **Production risk** | Low — disk space concern only |
| **Test required later** | No |

---

### 22. Clock skew / timestamp issue

| Field | Value |
|-------|-------|
| **Detection** | `generated_at` or `expires_at` in snapshot is in the future relative to consumer's clock, or significantly in the past |
| **Expected behaviour** | Consumer logs `clock_skew_detected`; uses snapshot if within reasonable tolerance (±60s) |
| **Fail-closed response** | If skew > 5 minutes: treat snapshot as `expires_at` logic may be unreliable; log warning |
| **Log message** | `snapshot_consumer: CLOCK_SKEW — delta={s}s` |
| **Alert required** | Yes, if > 5 minutes |
| **Production risk** | Medium — stale detection may malfunction |
| **Test required later** | Yes |

---

### 23. Partial file write

| Field | Value |
|-------|-------|
| **Detection** | Consumer reads file and JSON parse fails (`json.JSONDecodeError`) |
| **Expected behaviour** | Consumer treats file as invalid; does not use it |
| **Fail-closed response** | Prior valid file retained; consumer logs `json_parse_error` |
| **Log message** | `snapshot_consumer: JSON_PARSE_ERROR — path={path}; using prior valid file` |
| **Alert required** | Yes — indicates atomic write was not used |
| **Production risk** | Medium — indicates a worker bug (should use atomic write) |
| **Test required later** | Yes — inject partial JSON; verify consumer rejects |

---

### 24. Worker writes corrupted JSON

| Field | Value |
|-------|-------|
| **Detection** | Schema validator detects type errors or missing required fields |
| **Expected behaviour** | Validator rejects file; worker's atomic write fails; no overwrite |
| **Fail-closed response** | Same as partial file write — prior valid file retained |
| **Log message** | `worker: SCHEMA_VALIDATION_FAIL — path={path}, errors={errors}` |
| **Alert required** | Yes |
| **Production risk** | Medium |
| **Test required later** | Yes |

---

### 25. Worker crash / restart

| Field | Value |
|-------|-------|
| **Detection** | Process exits non-zero; supervisor/Docker detects; heartbeat file not updated |
| **Expected behaviour** | Supervisor restarts worker with exponential backoff; logs restart |
| **Fail-closed response** | Prior snapshots used until worker recovers; manifest may go stale |
| **Log message** | System-level restart log; `observability_worker: WORKER_RESTART — worker={name}` |
| **Alert required** | Yes, if > 3 restarts in 10 minutes |
| **Production risk** | Medium |
| **Test required later** | Yes |

---

### 26. Network outage

| Field | Value |
|-------|-------|
| **Detection** | Provider API calls return `ConnectionError` or timeout |
| **Expected behaviour** | Worker backs off; retries with backoff; logs `network_outage` |
| **Fail-closed response** | Workers fail gracefully; snapshots not updated; manifest may go stale; live bot degrades to prior universe |
| **Log message** | `provider_ingestion_worker: NETWORK_OUTAGE — provider={p}; retry={n}` |
| **Alert required** | Yes, if > 10 minutes |
| **Production risk** | High during outage |
| **Test required later** | Yes — mock connection error |

---

### 27. Secret missing

| Field | Value |
|-------|-------|
| **Detection** | `os.getenv(KEY)` returns empty string; worker logs `credentials_missing` |
| **Expected behaviour** | Worker skips affected provider; logs error; does not crash |
| **Fail-closed response** | Affected data source unavailable; downstream workers note reduced coverage |
| **Log message** | `provider_ingestion_worker: CREDENTIALS_MISSING — key={KEY_NAME}` (key NAME only, not value) |
| **Alert required** | Yes — configuration error |
| **Production risk** | High if Alpaca or FMP key is missing |
| **Test required later** | Yes |

---

### 28. Secret accidentally printed — expected never

| Field | Value |
|-------|-------|
| **Detection** | Log scanning for patterns matching API key format; `secrets_exposed = true` in any output file |
| **Expected behaviour** | **This must never happen.** All workers enforce `secrets_exposed = false`. Values are never passed to `str()` for logging. |
| **Fail-closed response** | If detected: rotate credentials immediately; audit logs |
| **Log message** | N/A — detected by external log scanner |
| **Alert required** | Yes — **immediate** |
| **Production risk** | Critical — credential compromise |
| **Test required later** | Yes — validate no output file contains key-shaped strings |

---

### 29. Backtest tool accidentally imported in production — expected never

| Field | Value |
|-------|-------|
| **Detection** | Import graph check; `import backtest_intelligence` or `import advisory_log_reviewer` found in `bot_trading.py` imports |
| **Expected behaviour** | **This must never happen.** Import boundary enforced by module classification. |
| **Fail-closed response** | CI/CD gate fails on import boundary violation |
| **Log message** | N/A — build-time detection |
| **Alert required** | Yes — build failure |
| **Production risk** | Medium — code bloat; possible unintended state access |
| **Test required later** | Yes — static import analysis test |

---

### 30. Live bot imports offline tool — expected never

| Field | Value |
|-------|-------|
| **Detection** | Import graph check; `import provider_fetch_tester`, `import factor_registry`, `import reference_data_builder` found in live bot import tree |
| **Expected behaviour** | **This must never happen.** These are offline diagnostic/build tools only. |
| **Fail-closed response** | CI/CD gate fails |
| **Log message** | N/A — build-time detection |
| **Alert required** | Yes — build failure |
| **Production risk** | Low direct risk; violates architecture boundary; risk of accidental API calls |
| **Test required later** | Yes — static import boundary test |
