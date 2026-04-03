# Unified Regime Detection Architecture — Decifer Trading
**Version:** 1.0
**Date:** 2026-04-01
**Status:** Reference architecture — no code changes this session
**Author:** Cowork (Amit to approve)

---

## Purpose

This document formalises the architectural decision to use the VIX proxy as the sole committed regime detector, integrates the four fragmented regime-related items into a coherent design, and defines the sequenced build order for remaining work. It supersedes scattered comments in config.py, signals.py, and roadmap/03-hmm-regime-detection.md as the single source of truth for regime architecture.

---

## 1. Current Regime Detection Stack

There are **three independent regime-sensing layers** already live. They are not redundant — each operates at a different granularity and serves a different purpose.

---

### Layer 1 — Macro Regime (4-state label)
**Function:** `scanner.get_market_regime()`
**Inputs:** SPY 1h OHLCV, QQQ 1h OHLCV, ^VIX 1h close (fallback: VIXY or implied SPY vol)
**Output:** one of `{BULL_TRENDING, BEAR_TRENDING, CHOPPY, PANIC, UNKNOWN}`
**Refresh:** once per scan cycle, cached as `_last_good_regime`

Classification logic (precedence top-to-bottom):
```
PANIC         if VIX > 35  OR  hourly VIX change > 20%
BULL_TRENDING if VIX < 15  AND SPY > 20-EMA  AND QQQ > 20-EMA
BEAR_TRENDING if VIX > 25  AND SPY < 20-EMA  AND QQQ < 20-EMA
CHOPPY        (all remaining cases)
UNKNOWN       (data sanity failure — bad VIX/SPY/QQQ values)
```

**What this layer controls:**
- Entry score threshold per regime: BEAR gets -3 offset (floor 15), CHOPPY gets -6 offset (floor 12), PANIC blocks all entries (threshold 99).
- `thesis_invalidation_regime_change`: open positions re-evaluated on significant regime shift.
- Position size multiplier from `_regime_size_mult()`: PANIC → 0.0, UNKNOWN → 0.75, all others → 1.0.

**VIX thresholds (config.py):**
| Key | Value | Meaning |
|-----|-------|---------|
| `vix_bull_max` | 15 | VIX ceiling for BULL_TRENDING |
| `vix_choppy_max` | 25 | VIX ceiling for BEAR_TRENDING classification |
| `vix_panic_min` | 35 | VIX floor for PANIC |
| `vix_spike_pct` | 20% | 1-hour VIX surge that forces PANIC regardless of level |

---

### Layer 2 — Signal Routing Regime (2-state label)
**Function:** `signals.get_market_regime_vix()`
**Inputs:** ^VIX spot reading
**Output:** `"momentum"` (VIX < 20) or `"mean_reversion"` (VIX >= 20)
**Refresh:** once per scan cycle; deduped — bot.py reuses the VIX already fetched by Layer 1 and passes it down via `score_universe(regime_router=...)`

This layer implements feat-regime-router (shipped 2026-03-30).

**What this layer controls:**
Dimension weight multipliers inside `compute_confluence()` via `_regime_multipliers()`:

| Regime | DIRECTIONAL MOMENTUM SQUEEZE FLOW BREAKOUT MTF | NEWS SOCIAL | REVERSION |
|--------|------------------------------------------------|-------------|-----------|
| `momentum` | ×1.3 | ×1.0 | ×0.7 |
| `mean_reversion` | ×0.7 | ×1.0 | ×1.3 |
| neutral / flag off | ×1.0 | ×1.0 | ×1.0 |

NEWS and SOCIAL are regime-neutral. They measure event-driven and sentiment signals, not market structure, so routing them is not warranted.

**A/B test control:** `regime_routing_enabled: False` in config.py disables all multipliers (equal-weight baseline) without code changes.

**Fallback on VIX fetch failure:** defaults to `"momentum"` — safe choice; prefers participating in a trend over sitting out.

---

### Layer 3 — VIX-Rank Adaptive Kelly (continuous)
**Function:** `risk.get_vix_rank()` / `risk.get_kelly_fraction()`
**Inputs:** ^VIX daily closes, trailing 252-day window
**Output:** `vix_rank` ∈ [0.0, 1.0] → `kelly_fraction` ∈ [0.50, 0.10]
**Refresh:** cached for 1 hour (`cache_ttl_seconds: 3600`)

**Formula:**
```
kelly_fraction = base_kelly × (1 - vix_rank × max_reduction)
```
Where `base_kelly = 0.50`, `max_reduction = 0.80`.
Example: rank=0.0 (calmest VIX in a year) → kelly=0.50; rank=1.0 (highest VIX in a year) → kelly=0.10.

**What this layer controls:**
Applied inside `calculate_position_size()` before the ATR vol cap. It compresses position size continuously as volatility rises — no binary jumps. The ATR vol cap is applied after Kelly; the more conservative of the two wins.

**Why continuous, not binary:** position sizing needs to degrade gracefully as fear increases. A binary switch from 50% to 10% Kelly would cause erratic sizing at the threshold. The linear rank formula provides smooth monotonic scaling.

---

### How the Three Layers Interact

```
scan cycle
   │
   ├── Layer 1: get_market_regime()
   │      VIX + SPY/QQQ EMA → {BULL, BEAR, CHOPPY, PANIC}
   │      → adjusts score threshold for entry eligibility
   │      → blocks all entries if PANIC
   │
   ├── Layer 2: get_market_regime_vix() [deduped — reuses Layer 1 VIX]
   │      VIX spot → {momentum, mean_reversion}
   │      → dimension weight multipliers in score_universe()
   │
   └── Layer 3: get_kelly_fraction() [independent, hourly cache]
           VIX percentile rank (252d) → kelly_fraction
           → scales position size in calculate_position_size()
```

All three layers read VIX. There is **no duplication**: Layer 1 uses VIX levels to assign discrete regime labels; Layer 2 uses the same VIX spot for dimension routing (deduped via bot.py); Layer 3 uses VIX *percentile rank* over a trailing year for sizing. These are three distinct computations on the same underlying variable.

The locked architectural decision (`config["regime_detector"] = "vix_proxy"`) is enforced by `tests/test_regime_architecture.py` and `ml_engine.RegimeClassifier.PRODUCTION_LOCKED = True`.

---

## 2. Hurst Exponent — Clarification of Two Distinct Concepts

There are two "Hurst" concepts in the project history. They must not be conflated.

---

### Concept A — R/S Hurst as a Per-Stock REVERSION Sub-Metric (REJECTED)

When building the REVERSION dimension (Dim 9) in signals.py, R/S Hurst was the first approach tried for confirming per-stock mean-reversion.

**Result:** unreliable on 60-bar windows. Trending stocks produced H=0.075 (should be H>0.5), mean-reverting stocks produced H=0.743 (should be H<0.5) — both wrong. The R/S estimator requires 500+ observations for stable estimates; 60-bar intraday or daily windows are far too short.

**Replacement:** Variance Ratio (Lo-MacKinlay, k=5), gated by ADF (Augmented Dickey-Fuller) test at p<0.05. VR is calibrated on 60-bar Monte Carlo (5000 simulations): ~8% false positive rate vs ~32% without the ADF gate. This is the current implementation.

**Decision: R/S Hurst is permanently rejected from the REVERSION dimension. VR + ADF gate + OU half-life + z-score is the validated architecture.**

**Documentation bug:** signals.py lines 1349-1355 contain a stale comment:
```python
# Composite of Hurst exponent + OU half-life + z-score.
# SAFETY: Hurst must confirm mean-reversion (H < 0.50) before
# z-score counts. Without this, we'd catch falling knives.
```
This is wrong. The actual implementation uses Variance Ratio, not Hurst. This should be updated to match the implementation. It is a comment bug, not a functional bug — the scoring logic is correct. Fix is included in the Step 1 build order below.

---

### Concept B — Hurst as a MARKET-LEVEL Regime Signal (PROPOSED, NOT YET BUILT)

A separate proposed feature: compute the Hurst exponent of the **SPY daily price series** over a 63-trading-day (quarterly) window using the DFA (Detrended Fluctuation Analysis) method — not R/S. At the market level with 63+ daily bars, DFA is substantially more reliable than R/S.

**Interpretation:**
- H > 0.55: SPY is in a persistent/trending regime → momentum strategies have edge
- H < 0.45: SPY is in an anti-persistent/mean-reverting regime → reversion strategies have edge
- 0.45 ≤ H ≤ 0.55: random walk regime → no reliable directional edge from either strategy type

**Where it fits:** as a second input to Layer 2, alongside the VIX threshold. Currently Layer 2 is purely VIX-driven. VIX measures fear and uncertainty. Hurst SPY measures whether the price series itself has momentum structure. These are orthogonal signals — VIX can be elevated while the market is still trending (e.g., volatile bull), and VIX can be calm while the market oscillates (e.g., low-vol chop). Using both improves routing precision.

**Proposed consensus rule for enhanced Layer 2:**
```
vix_regime   = "mean_reversion" if VIX >= 20 else "momentum"
hurst_regime = "trending" if H_spy > 0.55
               "reverting" if H_spy < 0.45
               "neutral"   otherwise

routing =
  if vix_regime == "momentum"      AND hurst_regime == "trending":   → strong_momentum
  if vix_regime == "mean_reversion" AND hurst_regime == "reverting":  → strong_reversion
  if signals disagree OR hurst_regime == "neutral":                   → neutral (all mults = 1.0)
```

This is more conservative than the current binary VIX router — multipliers only fire when two independent signals agree. When they conflict, fall back to equal-weight. This reduces false regime calls at the cost of fewer routed cycles.

**This feature is not built yet.** It requires validation of DFA Hurst stability on SPY daily data before connecting to the production pipeline.

---

## 3. Regime-Conditional Signal Weighting (SHIPPED)

This is fully live as of feat-regime-router (2026-03-30). The two-state VIX router applies dimension weight multipliers as described in Layer 2 above.

**What remains in this area:**

1. **Hurst extension to Layer 2** (Step 2 below) — adds a second confirming signal before routing fires.

2. **IC-weighted per-regime scoring** (feat-ic-weighted-scoring Phase 3, blocked on IC Phase 2 + 600+ regime-labeled trades) — replaces static multipliers (1.3/0.7) with IC-derived weights per regime. This is a Phase D item: it requires proving that per-regime IC differs significantly between momentum and mean-reversion regimes.

The A/B flag `regime_routing_enabled` enables measurement of lift from the existing routing. Run a week with it set to `False` (equal-weight baseline) and compare per-dimension IC and win rates. This data will inform whether the multiplier values (1.3/0.7) are calibrated correctly.

---

## 4. HMM Regime Detection — Deferral Rationale and Gate

HMM is deferred to Phase D (BACK-003). The reasoning is not purely about data volume — it is a multi-factor gate.

### Why HMM is deferred

**Reason 1: No cross-regime training data.**
200+ closed trades are required, distributed across at least two distinct market regimes (BULL + BEAR, or BULL + CHOPPY). The current trades are predominantly from a single regime window. An HMM trained on one regime is not an HMM — it is an overfitted label assigner.

**Reason 2: The current stack is not naive.**
HMM is often proposed as an improvement over "dumb" static systems. Decifer already has three regime-responsive layers. The marginal value of HMM over VIX-proxy + Hurst Layer 2 extension is unknown and may be small. We cannot measure that without IC Phase 2 data.

**Reason 3: Architectural scope.**
HMM outputs probabilities, not labels. Consuming probabilities requires redesigning Layer 1 (entry threshold is a step function of label, not a smooth function of probabilities) and Layer 2 (multipliers are per-label, not a probability-weighted blend). This is a Phase D refactor, not an incremental change.

**Reason 4: IC validation must come first.**
If the current VIX-proxy routing does not produce measurable IC lift vs the equal-weight baseline, the limiting factor is likely signal quality, execution slippage, or data coverage — not regime granularity. We need IC Phase 2 results to isolate regime as the bottleneck before building a more complex regime detector.

**Reason 5: PANIC stays a hard rule regardless.**
HMM is too slow to detect flash crashes (VIX spike > 20% in one hour). The PANIC hard gate remains VIX-based in any future architecture. This is risk management, not regime classification.

### Gate for revisiting HMM

All of the following must be met before HMM enters the active queue:

1. **Trade volume:** ≥ 200 closed trades distributed across at least two distinct Layer 1 regime states (not all BULL_TRENDING).
2. **IC Phase 2 complete:** `ic_min_threshold` raised to 0.03, Alphalens quantile analysis run, per-dimension IC measured per regime.
3. **Routing lift measured:** `regime_routing_enabled` A/B data shows VIX-proxy routing is producing less than 5% IC lift vs equal-weight baseline. If routing is already producing 10%+ lift, the bottleneck is elsewhere and HMM priority drops.
4. **Hurst Layer 2 extension built and validated:** Hurst + VIX consensus routing represents the intermediate step. Only if Hurst + VIX consensus is still insufficient does HMM become the next candidate.
5. **Amit explicit approval** after reviewing IC Phase 2 and routing A/B results.

**Expected earliest gate opening:** Phase C completion (200+ trades + IC Phase 2), estimated post-Phase B signals refactor.

### What HMM would do when built

If the gate opens, HMM replaces Layer 1 + Layer 2 as an integrated regime probability engine — it does not run alongside them. Running two regime detectors in parallel creates architectural incoherence (per roadmap/03-hmm-regime-detection.md).

Design:
- `hmmlearn.GaussianHMM` with 3 states (bull, bear, choppy), trained on SPY daily log returns + VIX level + VIX term structure (VIX/VIX3M) + 20-day realized vol + SPY-QQQ correlation.
- Output: probability vector `[p_bull, p_bear, p_choppy]`
- Layer 1 equivalent: hard label = argmax(probabilities), unless VIX > 35 → PANIC override
- Layer 2 equivalent: dimension weights = weighted blend of per-regime weight profiles
- Monthly retraining on expanding window; models persisted to `data/models/`

PANIC remains a VIX hard gate and is not learned by the HMM. This is non-negotiable.

---

## 5. Conflicts: Hurst Regime Filter vs. REVERSION Dimension

Three conflicts to manage.

---

### Conflict 1 — Documentation Confusion (LOW RISK)

signals.py line 1349 describes the REVERSION dimension as using "Hurst exponent" when it actually uses Variance Ratio. When the market-level Hurst regime filter is built, this stale comment will create confusion: engineers will read the REVERSION dimension comments, see "Hurst," and conflate it with the regime-level Hurst signal.

**Resolution:** Update the comment in signals.py to accurately describe the implementation (VR + ADF gate + OU half-life + z-score). No functional change. This is Step 1 in the build order.

---

### Conflict 2 — Regime Hurst "trending" Signal Suppresses REVERSION Scores for Mean-Reverting Stocks (MEDIUM RISK)

If the market-level Hurst regime filter classifies SPY as "trending" (H_spy > 0.55), and this causes the `mean_reversion` multiplier to not fire, the REVERSION dimension gets ×1.0 instead of ×1.3. This could reduce the contribution of valid per-stock mean-reversion setups during trending markets.

However, individual stocks can and do exhibit strong mean-reversion characteristics even when the broad market is trending — extended individual stocks, sector rotations, post-earnings drifts. Suppressing the REVERSION multiplier means these setups get less weight, not zero score.

**Resolution:**
- The REVERSION dimension's ADF gate (p < 0.05) + VR gate + OU half-life gate are the primary quality controls. A stock that passes all three statistical gates has genuine mean-reversion structure regardless of the market regime. These gates must not be conditioned on regime state.
- The regime multiplier (×1.3 or ×1.0 or ×0.7) is a weight modifier on top of a quality-gated score. It nudges portfolio allocation, it does not override statistical evidence.
- The consensus rule (both VIX and Hurst must agree before multipliers fire) means the suppression only occurs when both signals point away from reversion. In that strong-trending case, modestly reducing REVERSION weight is appropriate.

---

### Conflict 3 — VIX and Hurst Can Disagree (LOW RISK, DESIGN CONSIDERATION)

VIX ≥ 20 → VIX says mean_reversion. But H_spy > 0.55 → Hurst says trending. This is a real scenario: a sharp volatile sell-off in a structurally trending bull market will temporarily spike VIX while the price series still has positive autocorrelation from the prior trend.

If the old binary VIX router were applied in this scenario, it would shift weights toward mean-reversion when the market may still be resuming its trend — a false call.

**Resolution:** the consensus rule already handles this. VIX and Hurst disagree → neutral (all mults = 1.0). No routing fires. This is a feature, not a bug: uncertainty about regime means we should not tilt weights. The equal-weight baseline is the safe fallback.

The current VIX-only router will continue to make this error until the Hurst extension is built. This is an acceptable limitation of Phase 1.

---

## 6. Build Order

### Already Shipped
- [x] **Layer 1** — `scanner.get_market_regime()` (4-state macro regime, score thresholds)
- [x] **Layer 2** — `signals.get_market_regime_vix()` + `_regime_multipliers()` (2-state signal routing)
- [x] **Layer 3** — `risk.get_vix_rank()` / `risk.get_kelly_fraction()` (continuous VIX-rank Kelly)
- [x] **REVERSION Dim 9** — VR + ADF gate + OU half-life + z-score (Hurst rejected, VR used instead)
- [x] **Architecture guard** — `test_regime_architecture.py` locks `regime_detector = "vix_proxy"` and `RegimeClassifier.PRODUCTION_LOCKED = True`
- [x] **IC Phase 1** — rolling IC weights live in `ic_calculator.py` (ic_min_threshold = 0.0)

---

### Step 1 — Documentation Fix (no code logic change)
**When:** next session
**Effort:** ~15 minutes
**Deliverable:** Update stale comment in `signals.py` lines 1349-1355.

Replace:
```python
# Composite of Hurst exponent + OU half-life + z-score.
# SAFETY: Hurst must confirm mean-reversion (H < 0.50) before
# z-score counts. Without this, we'd catch falling knives.
```
With:
```python
# Composite of Variance Ratio (VR) + OU half-life + z-score,
# gated by ADF test (p < 0.05). ADF is the primary quality gate;
# VR and OU provide conviction; z-score provides direction.
# Note: R/S Hurst was evaluated and rejected (unreliable on <60 bars).
# See roadmap/04-mean-reversion-dimension.md for rationale.
```

---

### Step 2 — Hurst DFA Regime Signal for Layer 2 (Phase B / parallel with signals refactor)
**When:** after Step 1, during or after Phase B (direction-agnostic signals refactor)
**Effort:** 2-3 days
**Blocked on:** nothing — can be built independently

**Deliverables:**
- `signals.compute_hurst_dfa(series, n_points=63)` — DFA method, returns H ∈ [0.0, 1.0]
- `signals.get_hurst_regime_spy()` — fetches 63 days of SPY daily closes, computes H, classifies: "trending" / "reverting" / "neutral"
- Config keys: `hurst_regime_enabled: False` (ship disabled), `hurst_trending_threshold: 0.55`, `hurst_reverting_threshold: 0.45`, `hurst_lookback_days: 63`, `hurst_cache_ttl_seconds: 3600`
- `_regime_multipliers()` updated to accept both `vix_regime` and `hurst_regime`, apply consensus rule
- Unit tests: DFA output range, SPY fetch, consensus states, multiplier correctness

**Validation before enabling:**
- Compute H for SPY over 2020-2026 daily closes; verify H > 0.55 during clear trending periods (2023-2024 bull) and H < 0.45 during choppy ranges.
- Compare routing decisions vs VIX-only routing over historical regime periods; quantify disagreement rate.
- Enable `hurst_regime_enabled: True` only after validation confirms H is stable and meaningful on 63-bar SPY daily.

---

### Step 3 — IC Phase 2 (Phase C — requires ≥200 closed trades)
**When:** after 200+ closed trades are logged
**Effort:** 3 days
**Blocked on:** closed_trades ≥ 200 AND test suite ≥ 80%

**Deliverables:**
- Raise `ic_min_threshold` from 0.0 → 0.03 (noise floor per Phase 2 spec)
- Alphalens quantile return analysis per dimension
- Per-regime IC measurement: does IC differ significantly between momentum vs mean_reversion routing cycles?
- Per-dimension IC measurement: which dimensions show lift from regime routing vs equal-weight baseline?
- KEEP / REDUCE / REMOVE recommendation per dimension based on IC evidence
- A/B routing comparison: regime_routing_enabled=True IC vs False IC

**Output:** this data answers whether the VIX-proxy routing is producing measurable signal lift. If yes, tune the 1.3/0.7 multipliers from IC data rather than guessing. If no (< 5% lift), open the HMM gate.

---

### Step 4 — HMM Regime Detection (Phase D — gate-guarded)
**When:** after all gate conditions in Section 4 are met
**Effort:** 1-2 weeks
**Blocked on:** IC Phase 2 complete + ≥200 trades across ≥2 regimes + VIX-proxy routing lift < 5% + Amit approval

See roadmap/03-hmm-regime-detection.md for implementation design. Key constraint: HMM replaces Layer 1 + Layer 2 as an integrated unit — it does not run alongside the VIX proxy. PANIC (VIX > 35) remains a hard override that bypasses HMM regardless.

---

## Summary Table

| Feature | Status | Layer | Controls |
|---------|--------|-------|----------|
| 4-state VIX macro regime | Shipped | Layer 1 | Entry eligibility, score thresholds |
| 2-state VIX signal routing | Shipped | Layer 2 | Dimension weight multipliers |
| VIX-rank adaptive Kelly | Shipped | Layer 3 | Position size magnitude |
| REVERSION Dim 9 (VR+ADF+OU) | Shipped | Signal | Per-stock mean-reversion score |
| Fix stale Hurst comment | Step 1 | Docs | No functional change |
| Hurst DFA Layer 2 extension | Step 2 | Layer 2 | Adds consensus signal to routing |
| IC Phase 2 + Alphalens | Step 3 | Validation | Evidence for multiplier calibration |
| HMM (conditional) | Step 4 | Layers 1+2 | Replaces VIX proxy entirely if gate met |

---

*Approved by: Amit — 2026-04-01*
