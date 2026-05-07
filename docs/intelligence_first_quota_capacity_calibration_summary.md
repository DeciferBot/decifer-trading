# Intelligence-First Quota Capacity Calibration Summary

**Sprint:** 7H.3
**Status:** Advisory/calibration only. No production code changed. No symbols approved. No roster changes.
**Generated:** 2026-05-07T18:18:18Z
**Report:** `data/live/quota_capacity_calibration_report.json`

---

## 1. Scenario Comparison

| Scenario | Total Cap | Structural Cap | ETF Cap | Attention Cap | Candidate Count | Structural Used | ETF Used | Governed Excl. | COST | MSFT | PG | SNDK | WDC | IREN |
|----------|-----------|---------------|---------|--------------|----------------|----------------|----------|---------------|------|------|-----|------|-----|------|
| A_baseline | 50 | 20 | 10 | 15 | 50 | 20 | 9 | 10 | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| B_moderate | 75 | 35 | 15 | 20 | 75 | 35 | 9 | 0 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| C_production_candidate | 100 | 50 | 20 | 20 | 92 | 50 | 9 | 0 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| D_upper_bound | 125 | 65 | 25 | 25 | 112 | 65 | 9 | 0 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| E_stress | 150 | 80 | 30 | 30 | 132 | 80 | 9 | 0 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

---

## 2. Runtime Performance

| Scenario | Publisher Gen (ms) | Manifest Val (ms) | Reader Load (ms) | Total (ms) |
|----------|--------------------|------------------|-----------------|-----------|
| A_baseline | 436.3 | 9.6 | 1.7 | 451.0 |
| B_moderate | 9.4 | 11.5 | 2.1 | 25.3 |
| C_production_candidate | 10.2 | 15.5 | 2.1 | 30.1 |
| D_upper_bound | 22.5 | 14.5 | 2.5 | 42.2 |
| E_stress | 9.5 | 16.1 | 3.5 | 31.9 |

---

## 3. Quota Overflow Analysis

| Scenario | Structural Binding | Structural Overflow | ETF Binding | ETF Overflow | Attention Binding |
|----------|--------------------|--------------------|-----------|-----------|--------------------|
| A_baseline | True | 163 | False | 0 | False |
| B_moderate | True | 148 | False | 0 | False |
| C_production_candidate | True | 133 | False | 0 | True |
| D_upper_bound | True | 118 | False | 0 | True |
| E_stress | True | 103 | False | 0 | True |

---

## 4. Theme Representation

| Theme | A | B | C | D | E |
|-------|---|---|---|---|---|
| ai_compute_infrastructure | 0/1 | 1/1 | 1/1 | 1/1 | 1/1 |
| banks | 4/4 | 4/4 | 4/4 | 4/4 | 4/4 |
| data_centre_power | 4/4 | 4/4 | 4/4 | 4/4 | 4/4 |
| defence | 4/4 | 4/4 | 4/4 | 4/4 | 4/4 |
| defensive_quality | 0/5 | 5/5 | 5/5 | 5/5 | 5/5 |
| energy | 4/4 | 4/4 | 4/4 | 4/4 | 4/4 |
| memory_storage | 0/2 | 2/2 | 2/2 | 2/2 | 2/2 |
| quality_cash_flow | 1/4 | 4/4 | 4/4 | 4/4 | 4/4 |
| semiconductors | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 |
| small_caps | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 |

---

## 5. COST / MSFT / PG Inclusion Status

- **COST** (governance_gap_defect), A: excluded, B: included, C: included, D: included, E: included
- **MSFT** (governance_gap_defect), A: excluded, B: included, C: included, D: included, E: included
- **PG** (governance_gap_defect), A: excluded, B: included, C: included, D: included, E: included

## 6. SNDK / WDC / IREN Inclusion Status

- **SNDK** (already_governed_elsewhere), A: excluded, B: included, C: included, D: included, E: included
- **WDC** (already_governed_elsewhere), A: excluded, B: included, C: included, D: included, E: included
- **IREN** (already_governed_elsewhere), A: excluded, B: included, C: included, D: included, E: included

---

## 7. Recommendation

**Recommended cap:** total=75, structural=35
**Recommended scenario:** `B_moderate`

**Rationale:**
- Moderate expansion (75/35) includes COST, MSFT, PG without excessive noise risk.
- All EIL themes gain at least one single-name representative.
- Recommended as activation cap if Amit approves structural expansion.

**Whether 50 remains acceptable:** Yes, for the activation sprint. The current cap is sufficient to validate the handoff path. Expansion is a post-activation calibration decision.

**Whether activation should wait for quota change:** No. The governance gap defects (COST/MSFT/PG) require only Amit acknowledgement, not a quota change, before activation. A quota change is a separate design decision that can be made after the activation sprint demonstrates the handoff path is stable.

---

## 8. Safety Confirmation

| Check | Status |
|-------|--------|
| Production manifest overwritten | `false` |
| Production universe overwritten | `false` |
| No symbols approved | `true` |
| No thematic_roster.json changes | `true` |
| No universe_builder.py changes | `true` |
| No quota_allocator.py changes | `true` |
| No production code modified | `true` |
| handoff_enabled | `false` |
| enable_active_opportunity_universe_handoff | `false` |
| live_output_changed | `false` |
| broker_called | `false` |
| trading_api_called | `false` |
| llm_called | `false` |

