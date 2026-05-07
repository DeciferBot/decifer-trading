# Intelligence-First Log Retention and Diagnostics Policy

**Sprint:** 7H.1 — Operations readiness
**Status:** Pre-activation policy. Applies to current validation-only mode and future activation mode.
**Classification:** Advisory/design document. No production code changed.
**Reference:** See `docs/intelligence_first_cloud_deployment_runbook.md` Section 3 for directory definitions. See `docs/intelligence_first_daily_operator_checklist.md` Section 3b for daily rotation procedures.

---

## 1. Artefact Classification

Each file produced by the Intelligence-First system falls into one of four retention classes:

| Class | Definition | Examples |
|-------|-----------|---------|
| **A — System of record** | Must be retained permanently or for the life of the activation sprint. Never deleted without explicit approval. | `publisher_run_log.jsonl`, `current_manifest.json`, `active_opportunity_universe.json`, `handoff_publisher_observation_report.json` |
| **B — Rotating operational** | Retained for current session; archived or compressed after session close; oldest versions pruned on schedule | Bot logs (`data/logs/`), IBKR connection logs |
| **C — Diagnostic** | Retained until diagnosed and closed; pruned on schedule after diagnosis | `.fail_*.json` files in `data/live/`, `data/live/diagnostics/` |
| **D — Reference (read-only at runtime)** | Committed to repo; never modified at runtime; no retention policy needed | `data/reference/sector_schema.json`, `symbol_master.json`, `theme_overlay_map.json`, `factor_registry.json` |

---

## 2. Retention Policy by Artefact

### `data/live/publisher_run_log.jsonl` (Class A)

| Policy | Rule |
|--------|------|
| Retention | Permanent for the life of the activation sprint |
| Modification | Append-only. Never truncated, overwritten, or manually edited |
| Rotation | **Not rotated** during the current sprint. Once the activation sprint is fully closed and post-activation review is complete, rotation policy will be set by Amit explicitly |
| Backup | Include in any snapshot or backup of `data/live/` |
| Max size concern | At one line per 15-minute publisher cycle, ~96 lines/day; 2880 lines/month. File stays small indefinitely — rotation is not needed for size reasons |
| Git | **Never committed to repo** (operational file; contains timestamps) |

### `data/live/current_manifest.json` (Class A)

| Policy | Rule |
|--------|------|
| Retention | Always present during operation; overwritten each publisher cycle (publisher is atomic) |
| Archive | The previous manifest is overwritten in-place (no versioned history). If snapshot history is needed, see `docs/intelligence_first_snapshot_archive_design.md` |
| Git | Never committed to repo |

### `data/live/active_opportunity_universe.json` (Class A)

| Policy | Rule |
|--------|------|
| Retention | Always present during operation; overwritten each publisher cycle |
| Archive | Same as manifest — overwritten in-place |
| Git | Never committed to repo |

### `data/live/handoff_publisher_observation_report.json` (Class A)

| Policy | Rule |
|--------|------|
| Retention | Latest run always present; overwritten each observer run |
| History | Not retained between runs (latest-only) |
| Git | Never committed to repo |

### `data/heartbeats/handoff_publisher.json` (Class A during activation)

| Policy | Rule |
|--------|------|
| Retention | Latest heartbeat always present; overwritten each publisher run |
| History | Not retained between runs (latest-only) |
| Git | **Committed during validation phase** (heartbeat as evidence); rotate in production |

### `data/live/.fail_*.json` (Class C)

| Policy | Rule |
|--------|------|
| Retention | Retained until diagnosed |
| Rotation schedule | After 20 files accumulate in `data/live/`, move oldest to `data/live/diagnostics/` |
| Deletion | Only after diagnosis is recorded. Do not delete unread fail files |
| Git | Never committed to repo |

### Bot logs `data/logs/` (Class B)

| Policy | Rule |
|--------|------|
| Retention | Current session log retained in full |
| Rotation | Compress (gzip) and rename with date suffix when file exceeds 100 MB or at end of trading session |
| Max retained | Keep 14 days of compressed logs; prune older |
| Git | Never committed to repo |

### `data/intelligence/advisory_runtime_log.jsonl` (Class A)

| Policy | Rule |
|--------|------|
| Retention | Permanent for the life of the advisory observation sprint |
| Modification | Append-only. Never truncated or overwritten |
| Git | Never committed to repo |

### `data/universe_builder/active_opportunity_universe_shadow.json` (Class A)

| Policy | Rule |
|--------|------|
| Retention | Always present; overwritten each universe builder run |
| Git | Never committed to repo |

---

## 3. Container Inclusion and Exclusion

When building a Docker image or deploying to a cloud instance:

### Include in image / deploy

| Item | Reason |
|------|--------|
| All Python source files | Runtime code |
| `data/reference/` directory and contents | Read-only reference data; static at runtime |
| `scripts/` directory | Operational scripts |
| `tests/` directory | Smoke tests run at startup |
| `config.py` | Required at startup |

### Do NOT include in image / container filesystem

| Item | Reason |
|------|--------|
| `.env` | Secrets must be injected via environment variables, not baked into image |
| `data/live/` | Written at runtime; must be on persistent volume |
| `data/heartbeats/` | Written at runtime; must be on persistent volume |
| `data/logs/` | Written at runtime; must be on persistent volume |
| `data/intelligence/` | Written at runtime; must be on persistent volume |
| `data/universe_builder/` | Written at runtime; must be on persistent volume |
| `data/ic_validation_result.json` | Operational state; persistent volume |
| `data/training_records.jsonl` | Operational state; persistent volume |
| `data/event_log.jsonl` | Operational state; persistent volume |

### Volume mount point

All `data/` subdirectories must be on a persistent volume mounted at `/opt/decifer-trading/data/`. Config auto-detects repo root via `__file__`; all paths are relative.

---

## 4. Live Bot Read Policy

This section defines what the live bot may and may not read from the Intelligence-First file system.

| File | Live bot may read? | Conditions |
|------|--------------------|-----------|
| `data/live/current_manifest.json` | **Yes** — only when `enable_active_opportunity_universe_handoff = True` | Gated by handoff flag in `bot_trading.py` |
| `data/live/active_opportunity_universe.json` | **Yes** — only when flag is True | Same gate |
| `data/live/publisher_run_log.jsonl` | **No** | Run log is observer-only; live bot never reads it |
| `data/live/handoff_publisher_observation_report.json` | **No** | Observation report is operator-only; never read by live bot |
| `data/live/.fail_*.json` | **No** | Diagnostics are operator-only; live bot does not process failure files |
| `data/heartbeats/handoff_publisher.json` | **No** | Heartbeat is observer-only; live bot uses fail-closed logic in handoff_reader, not heartbeat |
| `data/intelligence/economic_candidate_feed.json` | **Yes** (advisory path only, when advisory enabled) | Gated by `intelligence_first_advisory_enabled` |
| `data/intelligence/advisory_runtime_log.jsonl` | **No** | Advisory log is reviewer-only |
| `data/reference/` | **Yes** | Read-only reference data; always accessible |

**Hard rule:** The live bot may never call any IBKR order submission endpoint from any intelligence worker, publisher, or observer code path. This boundary is enforced by AST import checks in the test suite.
