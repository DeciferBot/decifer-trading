# Intelligence-First Scanner-Only Removals Review Summary

**Sprint:** 7H.2
**Status:** Review and classification only. No symbols approved. No roster changes. No activation.
**Generated:** 2026-05-07
**Output file:** `data/intelligence/scanner_only_removals_review.json`
**Classification:** Advisory/design document. No production code changed.

---

## 1. Total Scanner-Only Removals Reviewed

| Metric | Value |
|--------|-------|
| Total scanner-only per comparison report | **208** |
| Symbols reviewed in this sprint | **160** |
| Unreviewed (below tracking threshold) | **48** |
| Review coverage | 77% |

The 48 unreviewed symbols were below the tracking threshold in the advisory runtime log (fewer than 2 appearances across 51 advisory records). They are pure scanner-discovery candidates with no advisory or coverage gap evidence. They are implicitly classified as `scanner_only_attention` pending future observation.

Source of 160 reviewed symbols:
- 50 from `paper_handoff_comparison_report.json` drop analysis (A–COST alphabetically)
- 110 from `coverage_gap_review.json` recurring_unsupported_current
- Deduplicated union: 160 unique

---

## 2. Category Counts

| Category | Count | Can Block Activation? |
|----------|-------|----------------------|
| `scanner_only_attention` | 59 | No |
| `future_theme_candidate` | 55 | No |
| `review_required` | 18 | No (Amit acknowledgement recommended) |
| `unknown_requires_provider_enrichment` | 19 | No |
| `governance_gap_defect` | **3** | **Yes — requires Amit acknowledgement** |
| `already_governed_elsewhere` | 3 | No |
| `rejected` | 3 | No |
| **Total** | **160** | |

---

## 3. Governance Gap Defects

**Count: 3 — COST, MSFT, PG**

These three symbols are present in `data/intelligence/economic_candidate_feed.json` (EIL-level governance exists) but absent from the shadow universe. The economic intelligence pipeline correctly sources them; exclusion occurs at the `universe_builder` quota/cap stage.

| Symbol | EIL Theme | Route Hint | Sector | Exclusion Reason |
|--------|-----------|-----------|--------|-----------------|
| COST | `defensive_quality` + `quality_cash_flow` | swing, watchlist | consumer_staples | Structural quota (20) full; ETF proxies (XLP, XLV, SPLV) cover defensive_quality theme in shadow |
| MSFT | `quality_cash_flow` | position, swing, watchlist | information_technology | Structural quota full; AAPL + QUAL cover quality_cash_flow theme in shadow |
| PG | `defensive_quality` | swing, watchlist | consumer_staples | Structural quota full; ETF proxies cover theme |

**Root cause:** The `defensive_quality` theme sends 8 symbols through the EIL pipeline (COST, JNJ, PG, KO, PEP, XLP, XLV, SPLV). Only the 3 ETF proxies (XLP, XLV, SPLV) make it into shadow because the structural quota is full. The individual names are correctly excluded — the ETF proxies represent the theme adequately.

**Is this a pipeline bug?** No. The pipeline is working as designed. The symbols are governed and sourced correctly; the structural quota prevents full materialisation. The `governance_gap_defect` classification flags this as needing deliberate acknowledgement before activation — not as a defect requiring a code fix.

**Required action before activation:** Amit must acknowledge that COST, MSFT, PG are EIL-governed but excluded from the shadow universe due to structural quota pressure, and that their absence during the activation window is acceptable. No code change required. If their inclusion is desired, the structural quota cap or routing must be adjusted in a separate sprint.

---

## 4. Symbols That May Deserve Future Theme Governance

**Count: 55** — These symbols have existing theme overlay mappings in `theme_overlay_map.json` but are not yet in the governed shadow universe. They are safe to remain scanner-only during the activation window. Roster addition is deferred to a future sprint.

**High-priority candidates** (multiple theme overlaps):

| Symbol | Themes | Sector | Priority |
|--------|--------|--------|---------|
| AMZN | cloud_computing, consumer_discretionary_retail, ecommerce_marketplace | consumer_discretionary | High — 3 themes, major ecosystem player |
| GOOGL | cloud_computing, internet_platform | communication_services | High — 2 themes, infrastructure layer |
| APP | enterprise_software, ai_application_software | information_technology | High — 2 themes, AI application direct |
| COIN | fintech, crypto_exchange | financials | Medium — 2 themes, crypto regime dependent |
| APO / BX | investment_banking, asset_management | financials | Medium — 2 themes, credit stress plays |
| SNAP | internet_platform, social_media | communication_services | Medium — 2 themes |

**Notable single-theme candidates:**

| Symbol | Theme | Notes |
|--------|-------|-------|
| RKLB | space_launch | Direct thematic fit; high occurrence in advisory log |
| IONQ | quantum_computing | Quantum computing theme; early-stage |
| OKLO | nuclear_power (energy_transition adjacent) | Nuclear power candidate; speculative |
| CRWD | cybersecurity | Major cybersecurity name; strong thematic fit |
| GE | aerospace / industrial_conglomerate | Data centre power adjacent |
| EOSE | battery_storage / energy_transition | Clean energy storage |
| NFLX | streaming / internet_platform | Streaming theme |
| INTC | semiconductor / compute | Semiconductor theme but market share declining |
| AMAT | semiconductor_equipment | Direct semiconductor equipment exposure |
| SMCI | data_centre_infrastructure | AI data centre direct beneficiary |

**Action:** No roster changes this sprint. Evaluate for future themed governance after activation sprint closes.

---

## 5. Symbols Safe to Remain Scanner-Only Attention

**Count: 59** — Pure scanner discovery (Tier D structural or dynamic source). No theme overlay. Not in economic feed. No governance path needed at this time.

These are predominantly:
- Financial sector names (regional banks, asset managers, insurance companies)
- Industrial/materials mid-caps with no AI/energy/defense thematic fit
- Consumer names without theme coverage
- Diversified companies that score well on momentum but lack thematic logic

**Representative sample:** ACGL, ADP, ADSK, AGCO, AON, APH, ASND, AVTR, AYI, B, BAP, BBY, BG, BIDU, BJ, BK, BR, BURL, CARR, CCI, CDE, CG, CHTR, CMS...

No action required. Scanner continues to discover these on signal merit alone.

---

## 6. Symbols Rejected or Requiring Enrichment

### Rejected (3)

| Symbol | Reason |
|--------|--------|
| BULL | 3× leveraged bull ETF. Redundant given SPY and QQQ already in shadow. No incremental value. |
| BMNR | Micro-cap mining name. No thematic fit. Scanner artefact below governance bar. |
| UAMY | Micro-cap antimony miner. No thematic fit. Scanner artefact. |

### Unknown / Requires Provider Enrichment (19)

| Symbol | Type | Notes |
|--------|------|-------|
| VXX | Volatility proxy (VIX futures ETP) | Already have SPLV in shadow for volatility. Needs enrichment before governance decision. |
| SVXY | Inverse volatility ETP | Inverse leveraged product. Needs enrichment. |
| SLV | Commodity proxy (silver ETF) | Already have GLD/USO. Needs enrichment before governing. |
| BITO | Crypto proxy (bitcoin futures ETF) | Already have IBIT in shadow. Needs enrichment. |
| MSTR | Bitcoin treasury company | Crypto-proxy. Needs enrichment. |
| FUTU | Foreign-listed broker (HK) | Limited US data coverage. Needs enrichment. |
| BF.B | Dual share class (Brown-Forman B) | Non-standard ticker. Needs clarification. |
| DDOG | Cloud monitoring | Coverage gap review flagged for enrichment. Reassess after FMP enrichment. |
| MRNA | Biotech (mRNA) | Coverage gap review flagged for enrichment. |
| REGN | Biotech (rare disease/oncology) | Coverage gap review flagged for enrichment. |
| TGT | Retail | Coverage gap review flagged for enrichment. |
| VRSN | Internet infrastructure | Coverage gap review flagged for enrichment. |
| WST | Medical/pharma packaging | Coverage gap review flagged for enrichment. |
| OSCR | Health insurance | Coverage gap review flagged for enrichment. |
| CBOE | Exchange operator | Coverage gap review flagged for enrichment. |
| XNDU | Unknown ticker | Cannot identify from available reference data. |
| YSS | Unknown micro-cap | Cannot identify. |
| INFQ | Unknown | Cannot identify. |
| EBAY | E-commerce | Coverage gap review flagged for enrichment. |

---

## 7. Whether Any Removals Should Block Activation

**3 symbols require Amit acknowledgement before activation: COST, MSFT, PG.**

This is NOT a hard block. The classification is `governance_gap_defect` because these symbols are EIL-governed (present in the economic candidate feed) but absent from the shadow universe. This is expected quota-pressure behavior, not a pipeline bug.

**The required action is acknowledgement, not a code fix.**

Specifically, before flag flip, Amit must confirm:

> "I acknowledge that COST, MSFT, and PG are governed by the economic intelligence layer (defensive_quality / quality_cash_flow themes) but excluded from the shadow universe due to structural quota pressure. The ETF proxies (XLP, XLV, SPLV, QUAL, AAPL) adequately represent these themes in the shadow universe. Their absence from the activation window is acceptable. No code change required."

If Amit does NOT acknowledge this, activation is blocked by the review criteria in `docs/intelligence_first_scanner_only_removals_review_plan.md` Section 5 (Activation Gate).

No other removal in this review blocks activation.

---

## 8. Recommendation

**Activation is not blocked by the 160 reviewed scanner-only removals, subject to one condition:**

Amit must acknowledge the 3 `governance_gap_defect` symbols (COST, MSFT, PG) as described in Section 7. This acknowledgement should be recorded in `docs/intelligence_first_handoff_activation_checklist.md` before the flag is flipped.

All other removals are acceptable:
- 59 `scanner_only_attention` symbols: scanner continues to discover these; no governance urgency
- 55 `future_theme_candidate` symbols: deferred to post-activation themed governance sprint
- 18 `review_required` symbols: warrant further review but do not block activation
- 19 `unknown_requires_provider_enrichment` symbols: enrichment deferred; do not block
- 3 `rejected` symbols: confirmed non-fits; do not govern
- 3 `already_governed_elsewhere` symbols: SNDK, WDC, IREN governed but quota-excluded; correct behavior

The 48 unreviewed symbols (below tracking threshold) are implicitly `scanner_only_attention` and do not block activation.

**Next steps after Amit acknowledgement of COST/MSFT/PG:**
1. Continue cross-session observation until `distinct_utc_sessions = 3`
2. At 3 sessions: run smoke + return full observation report
3. Stop and ask Amit before Sprint 7I (activation sprint)
4. Sprint 7I activation checklist Section 14 must include acknowledgement of governance gap defects

---

## Safety Confirmation

| Check | Status |
|-------|--------|
| No symbols approved in this sprint | `true` |
| No thematic_roster.json changes | `true` |
| No universe_builder.py changes | `true` |
| No candidate_resolver.py changes | `true` |
| No production code changes | `true` |
| No handoff flag flip | `true` |
| `live_output_changed` | `false` |
| `advisory_only` | `true` |
