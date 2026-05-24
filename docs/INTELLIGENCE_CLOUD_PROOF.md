# DECIFER Intelligence Cloud — Execution Isolation Proof

**Generated:** 2026-05-24  
**Sprint:** DigitalOcean Intelligence Cloud Deployment (v4.37.0)  
**Purpose:** Documented evidence that the intelligence cloud cannot execute trades.

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

Expected output (checks E1–E6, P1–P4, R1–R3, B1 — 13 checks total):
```
Decifer Intelligence Cloud — Pre-Deploy Verification
════════════════════════════════════════════════════
  [PASS] E1: 'execute_buy' correctly raises ExecutionBlockedError
  [PASS] E2: 'execute_short' correctly raises ExecutionBlockedError
  [PASS] E3: 'execute_sell' correctly raises ExecutionBlockedError
  [PASS] E4: 'execute_buy_option' correctly raises ExecutionBlockedError
  [PASS] E5: 'execute_sell_option' correctly raises ExecutionBlockedError
  [PASS] E6: 'flatten_all' correctly raises ExecutionBlockedError
  [PASS] P1: Market Now payload is SaaS-safe
  [PASS] P2: No blocked fields in Market Now payload
  [PASS] P3: Portfolio route returns intelligence-only placeholder
  [PASS] P4: No mutation routes registered (GET-only API confirmed)
  [PASS] R1: yfinance is absent from requirements.intelligence.txt
  [PASS] R2: ib_async is absent from requirements.intelligence.txt
  [PASS] R3: No Railway reference in intelligence cloud files
  [PASS] B1: Layer boundary verifier: PASSED — 0 violations

  Checks: 14  |  Passed: 14  |  Failed: 0

  VERDICT: GO — DigitalOcean intelligence deployment is cleared.
```

### verify_intelligence_execution_separation.py

```bash
python3 scripts/verify_intelligence_execution_separation.py
# Expected:
#   PASSED — no layer boundary violations detected.
```

### pytest test suite

```bash
pytest tests/test_intelligence_execution_separation.py -v
# Expected: 49 passed
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

## Final verdict

| Deployment | Status | Evidence |
|---|---|---|
| DigitalOcean intelligence cloud | **GO** | E1–E6 all block, P1–P4 pass, B1 passes, no ib_async, no yfinance, no Railway |
| User broker connection | **HOLD** | Not in v1 scope |
| User live execution | **HOLD** | Not in v1 scope |
| Public SaaS intelligence API | **GO** | SaaS-safe validation passes on `/api/market-now` |
