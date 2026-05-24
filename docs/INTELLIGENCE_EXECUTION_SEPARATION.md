# DECIFER Trading — Intelligence / Execution Separation

**Status:** Foundation complete (v4.37.0, Sprint: Intelligence Execution Separation Foundation)  
**Author:** Amit Chopra  
**Date:** 2026-05-24

---

## Strategic decision

**DECIFER Trading v1 is an intelligence product, not an execution product.**

The system's primary value is market intelligence: regime awareness, macro driver
identification, thematic opportunity generation, and synthesis via the Apex engine.
Execution — order placement, position sizing, broker connectivity — is a separate
concern that belongs on a dedicated, operator-managed node.

This document defines the architecture boundary between these two concerns and the
rules that enforce it.

---

## The two layers

### Intelligence Layer

The intelligence layer is everything the system knows about markets and opportunities.
It is safe to run in the cloud because it makes no broker calls.

**What it includes:**
- Full-context market intelligence (drivers, regime, themes)
- Macro driver identification (live_driver_resolver.py)
- Theme activation and thematic opportunity generation (theme_activation_engine.py, candidate_resolver.py)
- Sector leadership and catalyst clusters (catalyst_engine.py)
- Opportunity universe generation (universe_builder.py)
- Apex synthesis via Claude (apex_orchestrator.py, market_intelligence.py)
- Signal scoring (signals/, signal_pipeline.py)
- Risk notes and watchlist relevance
- SaaS-safe customer intelligence outputs (saas_intelligence_output.py, market_now_builder.py, mobile_api.py)

**What it does NOT include:**
- Any direct broker connection
- Order placement or modification
- Live position state (positions are execution state — the intelligence layer reads from handoff files, not live IBKR state)
- Account credentials or broker tokens

**Data connections remain internal.** The intelligence layer consumes Alpaca, FMP, Alpha Vantage, and FRED data internally. These raw data connections are never exposed to customers. What customers receive is the intelligence output — the synthesised, curated, plain-English market view.

### Execution Layer

The execution layer manages all broker interactions. It runs on the Mac (paper account DUP481326 today; future live account).

**What it includes:**
- IBKR/TWS connectivity (bot_ibkr.py, ibkr_reconciler.py)
- Order placement (orders_core.py, orders_options.py)
- Position mutation (orders_portfolio.py, orders_state.py)
- Order cancellation and modification (orders_guards.py, orders_contracts.py)
- Position sizing (position_sizing.py)
- Stop orders and take-profit orders (bracket_health.py)
- Pause/resume trading controls
- Smart execution (smart_execution.py)
- Bot orchestration (bot_trading.py, bot.py)

**What it does NOT do:**
- Serve customer-facing APIs
- Run in cloud/SaaS deployments
- Accept instructions from customer code paths

---

## Import boundary rules

These rules are **enforced by `scripts/verify_intelligence_execution_separation.py`**.
Violations cause the verifier to exit non-zero.

| Source layer | May import | May NOT import |
|---|---|---|
| `intelligence` | data_connector, shared_library | execution |
| `saas_output` | intelligence, shared_library | execution |
| `data_connector` | shared_library | execution |
| `execution` | intelligence, data_connector, shared_library | (no restrictions) |
| `shared_library` | (nothing from above layers ideally) | — |
| `dashboard_admin` | intelligence, shared_library | saas_output, execution direct order calls |
| `test_only` | any | — |

The key invariant: **neither the intelligence layer nor any customer-facing module
may import from the execution layer.** Execution code may consume intelligence
outputs; intelligence code must never call execution functions.

---

## Runtime modes

Configured via environment variables in `.env`:

| Variable | Default | Purpose |
|---|---|---|
| `DECIFER_RUNTIME_MODE` | `local_dev` | Active runtime mode |
| `DECIFER_EXECUTION_ENABLED` | `false` | Must be `true` to allow execution |
| `DECIFER_CUSTOMER_OUTPUT_MODE` | `false` | Enable customer-facing output |
| `DECIFER_MOBILE_READ_ONLY` | `true` | Mobile API is read-only |
| `DECIFER_DASHBOARD_CONTROL_ENABLED` | `false` | Dashboard execution controls |

### Mode definitions

**`local_dev`** (default)  
Development and exploration on the Mac. Execution is not implied. To run the paper
bot, switch to `paper_execution` and set `DECIFER_EXECUTION_ENABLED=true`.

**`intelligence_cloud`** (DigitalOcean / SaaS v1)  
Cloud deployment running the intelligence layer only. Execution is unconditionally
blocked — `assert_execution_allowed()` raises `ExecutionBlockedError` on every call,
regardless of any other environment variable. No order will ever reach a broker from
this mode.

**`paper_execution`** (Mac paper-trading node)  
The Mac running the paper bot (IBKR DU481326). Execution is allowed only when
`DECIFER_EXECUTION_ENABLED=true` is also set. Both conditions must be true.

**`full_trading`** (future live-trading node)  
Reserved for future live trading. Same enablement rules as `paper_execution`.

### Execution guard pattern

Every order-mutation function begins with:

```python
try:
    from runtime_config import assert_execution_allowed, ExecutionBlockedError
    assert_execution_allowed("action_name")
except ExecutionBlockedError as _exc:
    log.error("action_name: blocked by runtime guard — %s", _exc)
    return False  # or return, depending on function signature
except ImportError:
    pass  # runtime_config unavailable — degrade gracefully (legacy compatibility)
```

Functions guarded: `execute_buy`, `execute_short`, `execute_sell`,
`execute_buy_option`, `execute_sell_option`, `flatten_all`.

---

## SaaS-safe customer output

The `SaaSIntelligencePayload` dataclass (`saas_intelligence_output.py`) defines the
exact set of fields that may appear in customer-facing API responses.

### Allowed fields

| Field | Description |
|---|---|
| `market_regime_label` | Plain English regime summary ("Trending up", "Choppy") |
| `plain_english_summary` | 2–3 sentence market synthesis |
| `key_drivers` | List of active macro driver labels |
| `active_themes` | List of active thematic investment theme IDs |
| `opportunity_explanations` | Per-theme plain English explanation |
| `risk_notes` | Active risk factors in plain English |
| `what_to_watch` | Forward-looking monitoring items |
| `freshness_timestamp` | ISO-8601 UTC when the payload was built |
| `confidence_label` | "High" / "Moderate" / "Low" / "Insufficient data" |
| `source_category_labels` | Which source categories contributed |
| `data_entitlement_note` | Disclaimer text |

### Blocked fields (never in customer output)

Raw quotes (`bid`, `ask`, `last_price`, `volume`, `ohlcv`, `candles`),
option chain data (`strike`, `delta`, `gamma`, `iv`, `option_chain`),
broker state (`broker_account_id`, `order_id`, `position_size`, `stop_order`),
internal scores (`raw_score`, `ic_weight`, `execution_signal`),
provider payloads (`raw_news_payload`, `provider_payload`),
and PnL internals (`entry_price`, `exit_price`, `pnl`).

`validate_customer_payload(dict)` raises `SaaSPayloadValidationError` if any
blocked or unlisted field is present.

---

## Market Now

`market_now_builder.py` produces the canonical SaaS-safe market intelligence
snapshot by reading from persisted intelligence artefacts:

- `data/intelligence/live_driver_state.json` — macro drivers
- `data/intelligence/theme_activation.json` — active themes
- `data/live/current_manifest.json` — regime and freshness
- `data/apex_conversation_log.jsonl` — last Apex market read (optional)

The builder never calls Alpaca, FMP, or any live data provider. It never reads
IBKR state. It never produces execution signals. It is safe to call from any
cloud context.

---

## Current deployment topology

```
Mac (paper execution node)
  ├── bot.py          — full bot, paper_execution mode
  ├── bot_trading.py  — execution cycle
  ├── bot_ibkr.py     — IBKR connection (DU481326)
  └── run_intelligence_pipeline.py
         └── publishes: data/live/active_opportunity_universe.json
                        data/live/current_manifest.json

DigitalOcean (intelligence cloud node — v1 target)
  ├── run_intelligence_pipeline.py   — intelligence only, no execution
  ├── market_now_builder.py          — SaaS-safe payload builder
  ├── mobile_api.py                  — read-only mobile endpoints
  └── DECIFER_RUNTIME_MODE=intelligence_cloud
```

**The Mac remains the execution node.** The intelligence layer runs separately on
DigitalOcean, publishing handoff files that the Mac bot reads. They communicate via
persisted artefacts (JSON files), not a shared runtime.

---

## DigitalOcean: GO / HOLD status

| Decision | Status | Reasoning |
|---|---|---|
| DigitalOcean intelligence deployment | **GO** | Intelligence layer is execution-free; runtime guard blocks any accidental execution call; verifier confirms no boundary violations |
| User broker connection | **HOLD** | Not in v1 scope; requires auth, compliance review, and live trading infrastructure |
| User live execution | **HOLD** | Not in v1 scope; user-facing execution is a future phase after intelligence SaaS is validated |

---

## Future evolution

1. **DigitalOcean intelligence deployment** — run `run_intelligence_pipeline.py` on a cron,
   publish Market Now via an API endpoint, serve `mobile_api.py` behind auth.

2. **Read-only broker connection (future)** — customers see their own portfolio with
   Decifer intelligence overlaid. Still no execution from customer paths.

3. **Managed execution (future)** — customers authorise Decifer to trade on their behalf
   via a separate, audited execution path with explicit risk limits and kill switches.
   This is a separate product, not an extension of the intelligence SaaS.

---

*This document is the authoritative reference for the Intelligence/Execution boundary.
Update it whenever a new module is created or the runtime topology changes.*
