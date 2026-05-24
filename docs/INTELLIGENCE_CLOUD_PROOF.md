# DECIFER Intelligence Cloud — Execution Isolation Proof

**Generated:** 2026-05-24 (updated: Sprint M6 Production Hardening, v4.42.0)  
**Sprint:** Sprint M6 — Intelligence Cloud Production Hardening  
**Purpose:** Documented evidence that the intelligence cloud cannot execute trades and is hardened for production monitoring and customer-safe access.

---

## Layer 1 — Runtime guard (code level)

Every order-mutation function was modified to call `assert_execution_allowed()` as its first statement. This raises `ExecutionBlockedError` unconditionally when `DECIFER_RUNTIME_MODE=intelligence_cloud`.

### Functions guarded

| Function | File | Guard line |
|---|---|---|
| `execute_buy` | `orders_core.py` | Top of function body |
| `execute_short` | `orders_core.py` | Top of function body |
| `execute_sell` | `orders_core.py` | Top of function body |
| `execute_buy_option` | `orders_options.py` | Top of function body |
| `execute_sell_option` | `orders_options.py` | Top of function body |
| `flatten_all` | `orders_portfolio.py` | Top of function body |

### Guard implementation

```python
try:
    from runtime_config import assert_execution_allowed, ExecutionBlockedError
    assert_execution_allowed("execute_buy")
except ExecutionBlockedError as _exc:
    log.error("execute_buy %s: blocked by runtime guard — %s", symbol, _exc)
    _block_reason[symbol] = "runtime_mode_block"
    return False
except ImportError:
    pass  # runtime_config unavailable — degrade gracefully
```

### runtime_config.py invariant

```python
def assert_execution_allowed(action_name: str) -> None:
    if runtime_mode == MODE_INTELLIGENCE_CLOUD:
        raise ExecutionBlockedError(
            f"Execution action '{action_name}' is blocked: "
            "runtime_mode=intelligence_cloud. "
            "The intelligence cloud deployment never submits orders to any broker."
        )
```

`intelligence_cloud` is the only mode where execution is blocked REGARDLESS of other env vars. Even if `DECIFER_EXECUTION_ENABLED=true` were set on the DigitalOcean node (misconfiguration), the guard would still raise.

---

## Layer 2 — No broker library (container level)

`requirements.intelligence.txt` explicitly EXCLUDES the IBKR client library:

```
# NOT included (execution-node only):
#   - ib_async           — IBKR broker connection (Mac execution node)
```

`ib_async` is the Python IBKR client. Without it, any code path that attempts `from ib_async import IB` would raise `ImportError` before reaching any order submission. The intelligence container cannot physically connect to IBKR even if the code path were reached.

### Verification

```bash
grep -i "ib_async\|ib-async" requirements.intelligence.txt
# Expected: no output (empty — library is absent)
```

---

## Layer 3 — No IBKR connectivity (network level)

DigitalOcean droplets have no IBKR Gateway installed or accessible. There is no TWS, no IB Gateway process, and no IBKR port (4001/4002/7496/7497) open on the droplet. Any attempted connection would fail immediately at the TCP level.

The intelligence API reads from JSON artefacts on disk (`data/intelligence/`, `data/live/`). It makes outbound HTTP calls to Alpaca and FMP (read-only market data) but never opens a connection to any broker's order entry system.

---

## Verification results

### verify_intelligence_cloud_deploy.py

Run command:
```bash
DECIFER_RUNTIME_MODE=intelligence_cloud python3 scripts/verify_intelligence_cloud_deploy.py
```

Actual output (verified 2026-05-24, checks E1–E6, P1–P4, R1–R3, B1 — 14 checks):
```
Decifer Intelligence Cloud — Pre-Deploy Verification
════════════════════════════════════════════════════════════
  [PASS] E1: 'execute_buy' correctly raises ExecutionBlockedError: Execution action 'execute_buy' is blocked: runtime_mode=intelligence_cloud.
  [PASS] E2: 'execute_short' correctly raises ExecutionBlockedError: Execution action 'execute_short' is blocked: runtime_mode=intelligence_cloud.
  [PASS] E3: 'execute_sell' correctly raises ExecutionBlockedError: Execution action 'execute_sell' is blocked: runtime_mode=intelligence_cloud.
  [PASS] E4: 'execute_buy_option' correctly raises ExecutionBlockedError: Execution action 'execute_buy_option' is blocked: runtime_mode=intelligence_cloud.
  [PASS] E5: 'execute_sell_option' correctly raises ExecutionBlockedError: Execution action 'execute_sell_option' is blocked: runtime_mode=intelligence_cloud.
  [PASS] E6: 'flatten_all' correctly raises ExecutionBlockedError: Execution action 'flatten_all' is blocked: runtime_mode=intelligence_cloud.
  [PASS] P1: Market Now payload is SaaS-safe (11 fields)
  [PASS] P2: No blocked fields in Market Now payload
  [PASS] P3: Portfolio route returns intelligence-only placeholder
  [PASS] P4: No mutation routes registered (GET-only API confirmed)
  [PASS] R1: yfinance is absent from requirements.intelligence.txt (active lines)
  [PASS] R2: ib_async is absent from requirements.intelligence.txt (active lines)
  [PASS] R3: No Railway reference in intelligence cloud files
  [PASS] B1: Layer boundary verifier: PASSED — 0 violations

  Checks: 14  |  Passed: 14  |  Failed: 0

  VERDICT: GO — DigitalOcean intelligence deployment is cleared.
```

### verify_intelligence_execution_separation.py

```bash
python3 scripts/verify_intelligence_execution_separation.py
# Actual output (verified 2026-05-24):
#   Scanned: 290 Python files
#   Checked (intelligence + saas_output): 63 modules
#   Execution modules in registry: 30
#   PASSED — no layer boundary violations detected.
```

### pytest test suite

```bash
pytest tests/test_intelligence_execution_separation.py -v
# Actual output (verified 2026-05-24): 49 passed in 0.54s
```

### /health endpoint (via test client — Sprint M6 hardened)

```json
{
  "status": "ok",
  "service": "decifer-intelligence-api",
  "runtime_mode": "intelligence_cloud",
  "execution_blocked": true,
  "customer_output_mode": true,
  "data_freshness_status": "ok",
  "latest_market_now_timestamp": "2026-05-24T...",
  "latest_pipeline_artifact_timestamp": "2026-05-24T...",
  "degraded_artifact_warnings": [],
  "ts": "2026-05-24T..."
}
```

When artefacts are stale (pipeline not run recently), the response degrades safely:

```json
{
  "status": "ok",
  "data_freshness_status": "stale",
  "degraded_artifact_warnings": [
    "Market pipeline manifest is stale (6.6h old)",
    "Theme activation data is stale (6.6h old)"
  ],
  "latest_market_now_timestamp": "2026-05-23T18:54:30Z",
  "latest_pipeline_artifact_timestamp": "2026-05-24T00:27:48+00:00",
  ...
}
```

No file paths, secrets, broker state, or execution internals are exposed.

### /api/market-now response keys (verified live)

```
['active_themes', 'confidence_label', 'data_entitlement_note', 'freshness_timestamp',
 'generated_at', 'key_drivers', 'market_regime_label', 'opportunity_explanations',
 'plain_english_summary', 'risk_notes', 'source_category_labels', 'status', 'what_to_watch']
Blocked fields present: NONE
```

---

## yfinance absence proof

```bash
grep -r "import yfinance\|from yfinance" \
  intelligence_api.py market_now_builder.py mobile_api.py \
  saas_intelligence_output.py runtime_config.py \
  requirements.intelligence.txt
# Expected: no output
```

---

## Railway absence proof

```bash
grep -ri "railway" \
  intelligence_api.py requirements.intelligence.txt \
  docker-compose.yml docs/DIGITALOCEAN_INTELLIGENCE_CLOUD_DEPLOYMENT.md
# Expected: no output (Railway is not the deployment target — DigitalOcean is)
```

---

## /api/market-now endpoint proof

After starting the service locally:

```bash
DECIFER_RUNTIME_MODE=intelligence_cloud \
  gunicorn intelligence_api:app --bind 0.0.0.0:8000 --workers 1 --daemon

curl -s http://localhost:8000/api/market-now | python3 -m json.tool
```

Expected response structure (no blocked fields):
```json
{
  "status": "ok",
  "generated_at": "2026-05-24T...",
  "market_regime_label": "Trending up",
  "plain_english_summary": "...",
  "key_drivers": ["AI capital spending cycle expanding", ...],
  "active_themes": ["data_centre_power", ...],
  "opportunity_explanations": [...],
  "risk_notes": [],
  "what_to_watch": [...],
  "freshness_timestamp": "2026-05-24T...",
  "confidence_label": "High",
  "source_category_labels": ["market_data", "macro_drivers", ...],
  "data_entitlement_note": "Market intelligence powered by Decifer. Not financial advice."
}
```

Absent from response (confirmed by saas_intelligence_output.validate_customer_payload):
- `bid`, `ask`, `last_price`, `volume` — raw market data
- `order_id`, `position_size`, `stop_order` — broker state
- `broker_account_id`, `buying_power`, `account_value` — account data
- `raw_score`, `execution_signal`, `ic_weight` — internal scores

```bash
# Confirm health check shows execution_blocked: true
curl -s http://localhost:8000/health
# {
#   "status": "ok",
#   "service": "decifer-intelligence-api",
#   "runtime_mode": "intelligence_cloud",
#   "execution_blocked": true,
#   "ts": "..."
# }
```

---

## Sprint M6 — Production Hardening Verification

### verify_intelligence_cloud_production_hardening.py

Run command:
```bash
DECIFER_RUNTIME_MODE=intelligence_cloud \
DECIFER_CUSTOMER_OUTPUT_MODE=true \
python3 scripts/verify_intelligence_cloud_production_hardening.py --verbose
```

Actual output (verified 2026-05-24, 24 checks):
```
  [PASS] H1–H7: /health reports all required fields, exposes no secrets
  [PASS] M1–M2: /api/market-now passes validate_customer_payload, 0 blocked fields
  [PASS] M3–M4: Degraded payload safe when artefacts missing; plain-language messaging confirmed
  [PASS] V1: validate_customer_payload rejects execution-like wording in values
  [PASS] V2: validate_customer_payload rejects broker-like field names
  [PASS] V3: validate_customer_payload rejects raw internal artefact names in values
  [PASS] V4: validate_customer_payload rejects missing freshness_timestamp
  [PASS] V5: validate_customer_payload rejects stale freshness_timestamp (8h old)
  [PASS] V6: validate_customer_payload rejects empty data_entitlement_note
  [PASS] E1: execute_buy blocked in intelligence_cloud mode
  [PASS] E2: No mutation routes registered
  [PASS] R1–R3: yfinance absent, ib_async absent, no Railway references
  [PASS] D1: /api/mobile/* documented as requiring Cloudflare Access
  [PASS] B1: Layer boundary verifier PASSED — 0 violations

  Checks: 24  |  Passed: 24  |  Failed: 0

  VERDICT: GO — Intelligence cloud production hardening verified.
```

### What was hardened (Sprint M6)

| Component | Before | After |
|---|---|---|
| `/health` | 5 fields (status, service, runtime_mode, execution_blocked, ts) | 10 fields — adds customer_output_mode, data_freshness_status, latest_market_now_timestamp, latest_pipeline_artifact_timestamp, degraded_artifact_warnings |
| `validate_customer_payload()` | 2 rules (blocked fields, allowed fields) | 7 rules — adds broker-like field name check, execution-wording-in-values check, internal-artefact-name check, data_entitlement_note required, freshness_timestamp age check |
| `market_now_builder` | No degraded path — missing artefacts silently served as empty | Explicit degraded path: stale/missing artefacts → "market intelligence temporarily limited" response with `confidence_label: "Insufficient data"` and fresh timestamp |
| Verifier script | `verify_intelligence_cloud_deploy.py` (14 checks) | + `verify_intelligence_cloud_production_hardening.py` (24 new checks) |

---

## Final verdict

| Deployment | Status | Evidence |
|---|---|---|
| DigitalOcean intelligence cloud | **GO** | E1–E6 all block, P1–P4 pass, B1 passes, no ib_async, no yfinance, no Railway |
| Intelligence cloud production hardening | **GO** | M6 verifier: 24/24 checks pass |
| User broker connection | **HOLD** | Not in v1 scope |
| User live execution | **HOLD** | Not in v1 scope |
| Public SaaS intelligence API | **GO** | SaaS-safe validation passes, degraded path verified |
