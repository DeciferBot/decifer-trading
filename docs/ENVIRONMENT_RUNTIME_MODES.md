# DECIFER Trading — Environment & Runtime Modes

**Author:** Amit Chopra  
**Updated:** 2026-05-24 (v4.37.0)

---

## Overview

Every Decifer node must declare its runtime mode via `DECIFER_RUNTIME_MODE`. This single variable drives all execution guards, customer output controls, and safety invariants across the system.

---

## Runtime modes

| Mode | Intended node | Execution | Customer output |
|---|---|---|---|
| `local_dev` | Developer Mac | NOT enabled (default) | Not enabled |
| `intelligence_cloud` | DigitalOcean | **Unconditionally blocked** | Enabled |
| `paper_execution` | Mac paper bot | Enabled when `DECIFER_EXECUTION_ENABLED=true` | Not enabled |
| `full_trading` | Future live node | Enabled when `DECIFER_EXECUTION_ENABLED=true` | Not enabled |

---

## Node configurations

### Mac paper execution node

The Mac runs the paper bot (IBKR DUP481326) and the operational dashboard.

```env
# .env on Mac paper bot
DECIFER_RUNTIME_MODE=paper_execution
DECIFER_EXECUTION_ENABLED=true
DECIFER_CUSTOMER_OUTPUT_MODE=false
DECIFER_MOBILE_READ_ONLY=true
DECIFER_DASHBOARD_CONTROL_ENABLED=false
```

**Both variables must be set.** `paper_execution` alone does not enable execution — `DECIFER_EXECUTION_ENABLED=true` is also required. This is intentional: a node that is accidentally misconfigured as `paper_execution` will NOT start executing trades.

### DigitalOcean intelligence cloud node

The DigitalOcean droplet runs the intelligence pipeline and serves the Flask intelligence API. It **never** connects to IBKR and **never** submits orders. `assert_execution_allowed()` raises `ExecutionBlockedError` on every call, regardless of any other env var.

```env
# DigitalOcean App Platform env vars (set via DO control panel, not .env file)
DECIFER_RUNTIME_MODE=intelligence_cloud
DECIFER_EXECUTION_ENABLED=false
DECIFER_CUSTOMER_OUTPUT_MODE=true
DECIFER_MOBILE_READ_ONLY=true
DECIFER_DASHBOARD_CONTROL_ENABLED=false

# Data providers (read-only API calls — no order placement)
ANTHROPIC_API_KEY=<from DO secrets>
ALPACA_API_KEY=<from DO secrets>
ALPACA_SECRET_KEY=<from DO secrets>
ALPACA_BASE_URL=https://paper-api.alpaca.markets
FMP_API_KEY=<from DO secrets>
ALPHA_VANTAGE_KEY=<from DO secrets>
FRED_API_KEY=<from DO secrets>
```

Do NOT set `IBKR_ACTIVE_ACCOUNT`, `IBKR_PAPER_ACCOUNT`, or any IBKR variables on DigitalOcean. The intelligence cloud has no IBKR connection.

### Local development

```env
# .env for exploration / tests (default when DECIFER_RUNTIME_MODE is unset)
DECIFER_RUNTIME_MODE=local_dev
DECIFER_EXECUTION_ENABLED=false
```

Execution is NOT implied in `local_dev`. To run the paper bot locally, switch to `paper_execution` and set `DECIFER_EXECUTION_ENABLED=true`.

---

## Environment variables reference

| Variable | Default | Valid values | Purpose |
|---|---|---|---|
| `DECIFER_RUNTIME_MODE` | `local_dev` | `local_dev`, `intelligence_cloud`, `paper_execution`, `full_trading` | Node identity |
| `DECIFER_EXECUTION_ENABLED` | `false` | `true` / `false` | Explicit execution opt-in |
| `DECIFER_CUSTOMER_OUTPUT_MODE` | `false` | `true` / `false` | Enable customer-facing output |
| `DECIFER_MOBILE_READ_ONLY` | `true` | `true` / `false` | Enforce mobile read-only |
| `DECIFER_DASHBOARD_CONTROL_ENABLED` | `false` | `true` / `false` | Dashboard execution controls |
| `INTELLIGENCE_API_CORS_ORIGIN` | `*` | domain or `*` | CORS origin for intelligence API |

---

## Enforcement

`runtime_config.py` reads all variables at import time. The key guard:

```python
from runtime_config import assert_execution_allowed, ExecutionBlockedError

try:
    assert_execution_allowed("execute_buy")
except ExecutionBlockedError:
    # raised unconditionally in intelligence_cloud
    # raised in local_dev and paper_execution without DECIFER_EXECUTION_ENABLED=true
    return False
```

Every order-mutation function (`execute_buy`, `execute_sell`, `execute_short`, `execute_buy_option`, `execute_sell_option`, `flatten_all`) calls this guard as its first statement.

---

## Verification

```bash
# Confirm execution is blocked in intelligence_cloud mode:
python3 scripts/verify_intelligence_cloud_deploy.py

# Confirm no intelligence module imports execution:
python3 scripts/verify_intelligence_execution_separation.py

# Run the separation test suite:
pytest tests/test_intelligence_execution_separation.py -v
```

All three must pass before any DigitalOcean deployment.
