# Theme Transmission Graph (TTG) — Architecture & API Contract

Sprint: M12A  
Status: **Active — shadow/read-only**  
Execution gate: **NOT ACTIVE** — TTG candidates do not trigger live trading in this sprint.

---

## Purpose

The TTG is a customer-facing intelligence engine that maps economic drivers to investable names through a structured transmission chain:

```
driver → theme → subtheme → bucket → symbol
```

It answers the question: *"Which specific names benefit from this economic driver, and why?"*

The TTG sits in the **intelligence layer**, below the SaaS output boundary and completely above the execution layer. It never imports broker, order, or position logic.

---

## Data Files (`data/intelligence/theme_graph/`)

| File | Schema | Purpose |
|------|--------|---------|
| `theme_nodes.json` | `{id, label, type, plain_english_description, status, risk_note}` | All graph nodes: 10 drivers, 10 themes, 24 subthemes, ~50 buckets |
| `theme_edges.json` | `{from_id, to_id, reason, confidence, required_activation_evidence, direction, exposure_polarity}` | ~80 directed edges encoding transmission chains |
| `bucket_definitions.json` | `{bucket_id, parent_theme, definition, inclusion_rule, exclusion_rule, accepted_evidence_types, default_route_hint, status}` | ~50 bucket definitions with per-bucket evidence type gates |
| `symbol_exposures.json` | `{symbol, label, driver_id, theme_id, bucket_id, exposure_type, confidence, reason_to_care, evidence_basis, source_type, route_hint, status, risk_note, last_reviewed}` | ~125 symbol exposure records across all 10 themes |

These files are **static and deterministic** — no LLM, no live data, no broker state at the data layer.

---

## Evidence Gate

Only symbols with **accepted evidence basis** and **customer-visible status** appear in the customer-facing universe:

**Accepted:**
- `curated_reference` — hand-curated, reviewed by Amit
- `company_profile` — from official company description
- `official_source` — NRC, DoD, CHIPS Act records, official filings
- `filing` — SEC 10-K/10-Q segment disclosures
- `ETF_holding` — confirmed ETF holding in a thematic ETF
- `news_catalyst` — news confirming specific exposure with contract/earnings evidence
- `internal_symbol_master` — Decifer internal signal history

**Rejected (never active):**
- `LLM_only` — no factual anchor
- `keyword_only` — name match without substance
- `popular_online` — social media or generic lists
- `weak_co_mention` — indirect co-mention without causal evidence
- `generic_sector_match` — sector tag without company-specific evidence

**Customer-visible statuses:**
- `active` — visible in search, theme detail, and shadow candidates
- `monitor_only` (expressed as `route_hint: "Monitor only"`) — visible in theme detail and direct card lookup; excluded from general search results

**Suppressed statuses:**
- `needs_review` — excluded from all customer-facing routes
- `proposed` — excluded from all customer-facing routes

---

## Modules

| Module | Layer | Purpose |
|--------|-------|---------|
| `theme_graph.py` | `INTELLIGENCE` | Loads TTG data files, enforces evidence gate, builds reason paths, exposes public API |
| `theme_graph_api.py` | `SAAS_OUTPUT` | Flask Blueprint with 4 customer routes, validates against saas_intelligence_output |

**Import rules (hard):**
- `theme_graph.py` must NOT import any execution module
- `theme_graph_api.py` must NOT import any execution module
- Neither module may import `yfinance`

---

## Public API — `theme_graph.py`

```python
get_themes_list() -> list[dict]
```
Returns all 10 theme nodes with driver activation status. Each entry includes: `theme_id`, `label`, `plain_english_description`, `status`, `driver_ids`, `driver_active`, `risk_note`.

```python
get_theme_detail(theme_id: str) -> dict | None
```
Returns full theme detail: node metadata, buckets, and all evidence-gated symbols. Returns `None` for unknown `theme_id`.

```python
get_symbol_card(ticker: str) -> dict | None
```
Returns a CustomerSymbolCard with reason path, evidence basis, route hint, and risk note. Returns `None` if the ticker is unknown or fails the evidence gate. Case-insensitive.

```python
search(query: str) -> dict
```
Searches themes (by label/description) and evidence-gated active symbols (by ticker, label, reason-to-care). Returns `{themes, symbols, total}`. Monitor-only symbols are excluded from search results — they require direct card lookup.

```python
get_shadow_candidates() -> list[dict]
```
Returns all evidence-gated `status=active` symbols as shadow candidates for `universe_builder`. Each record carries `candidate_source = "theme_transmission_graph"`. **These must not trigger execution, order logic, or broker logic.** The universe builder shadow gate enforces this.

---

## HTTP API Routes (`theme_graph_api.py` Blueprint)

Registered on the Flask app in `intelligence_api.py`.

### `GET /api/intelligence/themes`
List all 10 themes with driver activation status.

```json
{
  "theme_graph_themes": [
    {
      "theme_id": "ai_energy_nuclear",
      "label": "AI Energy & Nuclear Revival",
      "plain_english_description": "...",
      "status": "active",
      "driver_ids": ["ai_capex_growth", "ai_compute_demand"],
      "driver_active": true,
      "risk_note": "Valuation crowding risk..."
    }
  ],
  "total": 10,
  "disclaimer": "..."
}
```

### `GET /api/intelligence/themes/<theme_id>`
Full theme detail: buckets, symbol list with reason paths.

```json
{
  "theme_graph_themes": [...],
  "theme_graph_buckets": [...],
  "theme_graph_reason_path": [
    {"symbol": "NVDA", "reason_path": ["AI Infrastructure Buildout", "AI Energy & Nuclear Revival", "AI Accelerators & Networking", "AI Compute Accelerators & Networking", "NVIDIA Corporation"]}
  ],
  "symbols": [...],
  "symbol_count": 27,
  "disclaimer": "..."
}
```

### `GET /api/intelligence/search?q=<query>`
Search themes and evidence-gated active symbols. Requires `?q=` parameter (max 200 chars).

```json
{
  "theme_graph_search_results": {
    "query": "nuclear",
    "themes": [...],
    "symbols": [...],
    "total": 11
  },
  "disclaimer": "..."
}
```

### `GET /api/intelligence/symbol/<ticker>`
CustomerSymbolCard with full transmission chain.

```json
{
  "theme_graph_symbol_card": {
    "symbol": "NVDA",
    "label": "NVIDIA Corporation",
    "theme_id": "ai_energy_nuclear",
    "theme_label": "AI Energy & Nuclear Revival",
    "bucket_id": "ai_compute_accelerators_networking",
    "bucket_label": "AI Compute Accelerators & Networking",
    "exposure_type": "direct_beneficiary",
    "confidence": 0.95,
    "reason_to_care": "NVIDIA dominates the AI accelerator market...",
    "reason_path": ["AI Infrastructure Buildout", "AI Energy & Nuclear Revival", "AI Accelerators & Networking", "AI Compute Accelerators & Networking", "NVIDIA Corporation"],
    "evidence_basis_label": "company_profile",
    "route_hint": "In focus",
    "status": "active",
    "risk_note": "Export controls and competitive pressure from AMD...",
    "driver_active": true,
    "theme_risk_note": "Valuation crowding risk..."
  },
  "theme_graph_reason_path": [...],
  "disclaimer": "..."
}
```

---

## SaaS Allowlist

Five new fields approved by Amit in Sprint M12A and registered in `saas_intelligence_output._ALLOWED_FIELDS`:

| Field | Content |
|-------|---------|
| `theme_graph_themes` | List of theme nodes |
| `theme_graph_buckets` | List of bucket definitions |
| `theme_graph_symbol_card` | Single CustomerSymbolCard dict |
| `theme_graph_reason_path` | List of `{symbol, reason_path}` dicts |
| `theme_graph_search_results` | `{query, themes, symbols, total}` |

All five are subject to the same `_validate_no_nested_blocked()` check as every other allowed field — execution wording, broker field names, and raw market data cannot appear inside these fields.

---

## CustomerSymbolCard — What's Included / Excluded

**Included:**
- `symbol`, `label`
- `theme_id`, `theme_label`
- `bucket_id`, `bucket_label`
- `exposure_type` (one of: direct_beneficiary, supply_chain_beneficiary, second_order_beneficiary, etf_basket, pressure_or_negative, conditional)
- `confidence` (float 0–1)
- `reason_to_care` (plain-English, curated)
- `reason_path` (list of labels: driver → theme → subtheme? → bucket → symbol)
- `evidence_basis_label`
- `route_hint` (one of: "In focus", "On the radar", "Monitor only", "ETF route", "Needs review")
- `status`
- `risk_note`
- `driver_active` (bool — is the linked driver currently live in live_driver_state.json?)
- `theme_risk_note`

**Never included:**
- Buy/sell instruction, entry/exit levels
- Order language (stop, limit, target)
- Account or portfolio data
- Position sizing
- Broker fields
- Execution readiness indicator

---

## 10 Theme Packs

| Theme ID | Theme Name | Live Driver(s) |
|----------|-----------|---------------|
| `ai_energy_nuclear` | AI Energy & Nuclear Revival | `ai_capex_growth`, `ai_compute_demand` |
| `glp1_metabolic_health` | GLP-1 and Metabolic Health | `glp1_adoption` (TTG-specific) |
| `defence_rearmament` | Defence Rearmament & Security | `geopolitical_risk_rising` |
| `cybersecurity_digital_resilience` | Cybersecurity & Digital Resilience | `ai_capex_growth` (cyber attach) |
| `reshoring_industrial_capex` | Reshoring & Industrial Capex | `domestic_manufacturing_investment` |
| `housing_rate_sensitivity` | Housing & Rate Sensitivity | `yields_falling` |
| `water_infrastructure` | Water Infrastructure | `water_stress` |
| `critical_minerals_copper` | Critical Minerals & Copper | `electrification` |
| `gold_real_assets` | Gold & Real Assets | `reserve_diversification` |
| `digital_assets_infrastructure` | Digital Assets Infrastructure | `crypto_liquidity` |

Drivers listed as "TTG-specific" are not yet wired to live sensors. Their themes always load with `driver_active: false` but are always visible for customer discovery.

---

## Universe Builder Integration — Shadow Gate

`get_shadow_candidates()` returns evidence-gated active symbols. The universe builder may consume these as a **shadow candidate source** — enriching the handoff file with TTG-sourced names for Apex to consider.

**This sprint (M12A): shadow/read-only.**
- TTG candidates carry `candidate_source = "theme_transmission_graph"`
- Universe builder must check `candidate_source` and apply the shadow gate
- Shadow candidates must NOT flow through to execution, order logic, or broker calls
- The activation gate for TTG-sourced live entries requires explicit Amit approval in a future sprint

---

## Recommended Frontend Integration (Next Sprint)

The TTG API is designed for the following UI surfaces in the mobile customer app:

1. **Theme Map tab** (`ThemeMapTab.tsx`) — card grid of all 10 themes, each showing active/reference status, driver-active badge, and symbol count. Tap → theme detail page.

2. **Theme detail page** — theme description, bucket list, symbol cards sorted by confidence. Each symbol card shows route hint chip (In Focus / On the Radar / Monitor Only / ETF Route).

3. **Symbol deep-dive** — reason path breadcrumb (driver → theme → subtheme → bucket → symbol) as the primary data visualisation. Confidence ring. Risk note footer.

4. **Search overlay** — live search across themes and symbols. Results grouped: Themes | Names.

Suggested integration approach:
- Fetch `GET /api/intelligence/themes` on tab mount (light, ~10 themes)
- Lazy-load `GET /api/intelligence/themes/<id>` on tap
- Debounce `GET /api/intelligence/search?q=` at 300ms
- `GET /api/intelligence/symbol/<ticker>` when a symbol is tapped from any context

No authentication required on these routes (intelligence_cloud deployment). All routes are GET-only.

---

## Test Coverage

47 tests in `tests/test_theme_graph.py` covering:
- Determinism (4 tests)
- Reason paths (4 tests)
- Evidence gate (8 tests, 5 parameterised)
- Negative/pressure exposure labelling (3 tests)
- API field validation (5 tests)
- Layer separation (4 tests)
- Shadow candidates (3 tests)
- Theme coverage (6 tests)
- Search and card lookup (7 tests)
