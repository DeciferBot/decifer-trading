"""
Tier D deterministic test scan — no IBKR, no order execution.

Exercises the full Tier D pipeline:
  1. Load committed universe + PRU (Tier D)
  2. Get market regime (Alpaca/yfinance, ib=None)
  3. Run signal_pipeline (scores all symbols, tags Tier D, skips persistence)
  4. Run guardrails filter (empty open_symbols)
  5. Apply apex cap inline (writes stage=apex_cap to tier_d_funnel.jsonl)
  6. Mock Apex classification (all Tier D → POSITION, non-Tier-D → SWING)
  7. Run validate_entry for each Tier D signal (shadow path exercised)
  8. Print 14-item funnel report

Invariants preserved:
  - position_research_shadow_mode=True  (read from config, never overridden)
  - position_research_allow_live_position_entries=False  (same)
  - No orders placed at any layer
  - No training_records written (no execute_buy/execute_short called)

Usage:
    python3 scripts/tier_d_test_scan.py
"""

from __future__ import annotations

import json
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

# Ensure repo root is on path
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

UTC = timezone.utc

# ── 1. Environment check ──────────────────────────────────────────────────────
print("=" * 70)
print("TIER D TEST SCAN — deterministic, no IBKR, shadow mode")
print("=" * 70)

try:
    import anthropic  # noqa: F401
    import pandas  # noqa: F401
except ImportError as e:
    print(f"[FATAL] Missing dependency: {e}. Run: bash scripts/setup.sh")
    sys.exit(1)

# ── 2. Import core modules ────────────────────────────────────────────────────
import scanner
from signal_pipeline import run_signal_pipeline
from scanner import get_dynamic_universe, get_market_regime, get_position_research_universe
from guardrails import filter_candidates
from trade_context import build_context as build_trade_context
from entry_gate import validate_entry

# ── 3. Verify shadow mode flags from config ───────────────────────────────────
from config import CONFIG
_eg_cfg = CONFIG.get("entry_gate", {})
_shadow_mode = _eg_cfg.get("position_research_shadow_mode", True)
_allow_live = _eg_cfg.get("position_research_allow_live_position_entries", False)

print(f"\nConfig flags:")
print(f"  position_research_shadow_mode              = {_shadow_mode}")
print(f"  position_research_allow_live_position_entries = {_allow_live}")

if not _shadow_mode:
    print("[WARNING] shadow_mode is OFF — this test scan expects shadow mode ON")
if _allow_live:
    print("[ERROR] allow_live_position_entries is TRUE — this test scan must not place orders")
    sys.exit(1)

# ── 4. Get market regime ──────────────────────────────────────────────────────
print("\nFetching market regime (no IBKR — Alpaca/yfinance only)...")
try:
    regime = get_market_regime(None)  # ib not used in the function body
    print(f"  Regime: {regime.get('regime')}  VIX: {regime.get('vix', 0):.1f}")
except Exception as e:
    print(f"  [WARN] Regime fetch failed: {e}. Using safe default TRENDING_UP.")
    regime = {
        "regime": "TRENDING_UP",
        "vix": 18.0,
        "vix_1h_change": 0.0,
        "vix_change_1d": 0.0,
        "spy_price": 550.0,
        "spy_above_200d": True,
        "qqq_price": 460.0,
        "qqq_above_200d": True,
        "position_size_multiplier": 1.0,
        "regime_router": "momentum",
        "session_character": "MOMENTUM_BULL",
    }

# ── 5. Load universe (Tier A + B + C + D) ────────────────────────────────────
print("\nLoading universe (Tier A/B/C/D)...")
try:
    universe = get_dynamic_universe(None, regime)
except Exception as e:
    print(f"  [WARN] get_dynamic_universe failed: {e}. Using Tier A only.")
    from scanner import CORE_SYMBOLS, CORE_EQUITIES
    universe = list(set(CORE_SYMBOLS) | set(CORE_EQUITIES))

# Load Tier D metadata for later
tier_d_syms, tier_d_meta = get_position_research_universe()
_td_loaded = len(tier_d_syms)
_td_in_universe = len([s for s in tier_d_syms if s in universe])

print(f"  Total universe: {len(universe)} symbols")
print(f"  Tier D loaded from PRU: {_td_loaded}")
print(f"  Tier D in active universe: {_td_in_universe}")

# ── 6. Run signal pipeline ────────────────────────────────────────────────────
print("\nRunning signal pipeline (this takes 1-3 min for full universe)...")
try:
    pipeline = run_signal_pipeline(
        universe=universe,
        regime=regime,
        strategy_mode={},
        session="REGULAR",
        favourites=[],
        ib=None,
    )
except Exception as e:
    print(f"[FATAL] Signal pipeline failed: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)

all_scored = pipeline.all_scored or []
signals = pipeline.signals or []
tier_d_funnel = pipeline.tier_d_funnel or {}

# Count Tier D at pipeline stages
_td_all_scored = sum(1 for s in all_scored if s.get("scanner_tier") == "D")
_td_signals = sum(1 for s in signals if getattr(s, "scanner_tier", "") == "D")

print(f"  all_scored (all symbols): {len(all_scored)}")
print(f"  Tier D in all_scored: {_td_all_scored}")
print(f"  signals (passed all gates): {len(signals)}")
print(f"  Tier D in signals: {_td_signals}")

# Pull stage counts from funnel dict if available
_td_above_thresh = tier_d_funnel.get("td_above_thresh", "n/a")
_td_passed_strategy = tier_d_funnel.get("td_passed_strategy", "n/a")
_td_passed_persistence = tier_d_funnel.get("td_passed_persistence", "n/a")
_td_rescued = tier_d_funnel.get("td_rescued", "n/a")
_td_pipeline_output = tier_d_funnel.get("td_pipeline_output", _td_signals)

# ── 6b. Stale-data fallback: synthetic Tier D candidates ─────────────────────
# On Sunday / after hours, all 5m bars are stale and score_universe aborts.
# To still exercise the shadow/validate_entry path, inject synthetic candidate
# dicts for the first 30 PRU symbols at a minimal score. These do NOT represent
# real signals — they are scaffolding to confirm the shadow gate works.
_synthetic_mode = False
if _td_all_scored == 0 and _td_loaded > 0:
    print("\n  [INFO] Pipeline returned 0 scored symbols (stale weekend data).")
    print("  Injecting synthetic Tier D candidates to exercise shadow path...")
    _synthetic_mode = True
    _synthetic_candidates = []
    for _sym in list(tier_d_syms)[:30]:
        _pru_entry = tier_d_meta.get(_sym, {})
        _synthetic_candidates.append({
            "symbol": _sym,
            "score": 6.0,  # minimum floor — deliberate low score for testing
            "scanner_tier": "D",
            "direction": "LONG",
            "price": 0.0,
            "instrument": "stock",
            "discovery_score": _pru_entry.get("discovery_score", 0),
            "matched_position_archetypes": _pru_entry.get("matched_position_archetypes", []),
            "position_research_universe_member": True,
        })
    all_scored = _synthetic_candidates
    _td_all_scored = len(_synthetic_candidates)
    print(f"  Synthetic candidates injected: {len(_synthetic_candidates)}")

# ── 7. Guardrails filter ──────────────────────────────────────────────────────
print("\nRunning guardrails filter (empty open_symbols)...")
try:
    filtered_candidates = filter_candidates(
        all_scored,
        open_symbols=set(),
        regime=regime,
        minutes_to_close=None,
    )
except Exception as e:
    print(f"  [WARN] guardrails.filter_candidates failed: {e}. Using all_scored.")
    filtered_candidates = all_scored

_td_after_guardrails = sum(1 for c in filtered_candidates if c.get("scanner_tier") == "D")
print(f"  After guardrails: {len(filtered_candidates)} candidates")
print(f"  Tier D after guardrails: {_td_after_guardrails}")

# ── 8. Apex cap (inline, mirrors bot_trading.py logic) ───────────────────────
_CAP_LIMIT = 30
_cut_all_sorted = sorted(filtered_candidates, key=lambda c: c.get("score", 0), reverse=True)
_cut_candidates = _cut_all_sorted[:_CAP_LIMIT]
_cap_dropped = _cut_all_sorted[_CAP_LIMIT:]

_td_before_cap = [c for c in _cut_all_sorted if c.get("scanner_tier") == "D"]
_td_after_cap  = [c for c in _cut_candidates  if c.get("scanner_tier") == "D"]
_td_dropped_cap = [c for c in _cap_dropped     if c.get("scanner_tier") == "D"]

_min_sel_score = min((c.get("score", 0) for c in _cut_candidates), default=None)
_max_td_score  = max((c.get("score", 0) for c in _td_before_cap), default=None)
_highest_td_dropped_score = max((c.get("score", 0) for c in _td_dropped_cap), default=None)

# Write apex_cap record
_cap_record = {
    "ts":                            datetime.now(UTC).isoformat(),
    "stage":                         "apex_cap",
    "scan_type":                     "test_scan",
    "raw_candidates_before_cap":     len(_cut_all_sorted),
    "raw_tier_d_before_cap":         len(_td_before_cap),
    "raw_non_tier_d_before_cap":     len(_cut_all_sorted) - len(_td_before_cap),
    "cap_limit":                     _CAP_LIMIT,
    "selected_candidates_after_cap": len(_cut_candidates),
    "selected_tier_d_after_cap":     len(_td_after_cap),
    "selected_non_tier_d_after_cap": len(_cut_candidates) - len(_td_after_cap),
    "dropped_by_cap_total":          len(_cap_dropped),
    "dropped_tier_d_by_cap":         len(_td_dropped_cap),
    "dropped_non_tier_d_by_cap":     len(_cap_dropped) - len(_td_dropped_cap),
    "selected_tier_d_symbols":       [c.get("symbol") for c in _td_after_cap],
    "dropped_tier_d_symbols_top_20": [c.get("symbol") for c in _td_dropped_cap[:20]],
    "top_10_selected_by_score": [
        {"symbol": c.get("symbol"), "score": c.get("score"), "scanner_tier": c.get("scanner_tier", "")}
        for c in _cut_candidates[:10]
    ],
    "top_10_dropped_tier_d": [
        {
            "symbol":             c.get("symbol"),
            "score":              c.get("score"),
            "discovery_score":    c.get("discovery_score"),
            "matched_archetypes": c.get("matched_position_archetypes", []),
        }
        for c in _td_dropped_cap[:10]
    ],
    "max_tier_d_score_before_cap":    _max_td_score,
    "min_selected_score_after_cap":   _min_sel_score,
    "highest_dropped_tier_d_score":   _highest_td_dropped_score,
    "tier_d_with_archetypes_dropped": any(c.get("matched_position_archetypes") for c in _td_dropped_cap),
    "tier_d_strong_discovery_dropped": any((c.get("discovery_score") or 0) >= 6 for c in _td_dropped_cap),
}

_funnel_path = _REPO / "data" / "tier_d_funnel.jsonl"
try:
    with open(_funnel_path, "a") as _f:
        _f.write(json.dumps(_cap_record) + "\n")
    print(f"\nApex cap record written to {_funnel_path}")
except Exception as e:
    print(f"  [WARN] apex_cap write failed: {e}")

print(f"  Before cap: {len(_cut_all_sorted)} candidates ({len(_td_before_cap)} Tier D)")
print(f"  After cap:  {len(_cut_candidates)} candidates ({len(_td_after_cap)} Tier D)")
print(f"  Dropped:    {len(_cap_dropped)} candidates ({len(_td_dropped_cap)} Tier D)")

# ── 9. Mock Apex + shadow path validation ─────────────────────────────────────
# Mock classification: Tier D → POSITION (worst-case shadow path)
#                      non-Tier-D → SWING (no shadow work needed)
# This is the most adversarial mock: every Tier D candidate attempts
# full POSITION validation, maximising shadow_blocked records.

print("\nRunning mock Apex + shadow validation...")

# Build a signal lookup by symbol for validate_entry context
_sig_by_sym: dict[str, object] = {s.symbol.upper(): s for s in signals}

_td_reaching_validate = 0
_td_shadow_blocked = 0
_td_would_pass = 0
_td_would_fail_reason: dict[str, int] = {}
_orders_placed = 0

from trade_context import build_context as _build_tc
try:
    from earnings_calendar import get_earnings_days as _geda
except ImportError:
    _geda = None

for cand in _cut_candidates:
    sym = (cand.get("symbol") or "").upper()
    is_tier_d = cand.get("scanner_tier") == "D"
    if not is_tier_d:
        continue

    # Build TradeContext for this candidate
    sig = _sig_by_sym.get(sym)
    ctx = None
    try:
        _ed = None
        try:
            _ed = _geda(sym) if _geda else None
        except Exception:
            pass
        ctx = _build_tc(
            symbol=sym,
            direction=cand.get("direction", "LONG"),
            signal=sig,
            current_price=float(cand.get("price") or (sig.price if sig else 0) or 0),
            earnings_days_away=_ed,
            regime=regime.get("regime"),
        )
    except Exception as e:
        print(f"  [WARN] TradeContext build failed for {sym}: {e}")

    raw_score = round(float(cand.get("score") or 0) * 5)
    pru_snap = tier_d_meta.get(sym, {}).get("pru_fmp_snapshot") or {}

    _td_reaching_validate += 1
    try:
        gate_ok, gate_type, gate_reason, eff_score = validate_entry(
            direction=cand.get("direction", "LONG"),
            ctx=ctx,
            score=raw_score,
            opus_trade_type="POSITION",  # mock: Apex said POSITION
            score_breakdown=cand.get("dimension_scores") or {},
            instrument="stock",
            open_intraday_count=0,
            scanner_tier="D",
            pru_fmp_snapshot=pru_snap or None,
            tier_d_backfill_info=None,
        )
        if not gate_ok and gate_type == "POSITION_RESEARCH_ONLY":
            _td_shadow_blocked += 1
        elif gate_ok:
            _td_would_pass += 1
        else:
            key = gate_reason[:60] if gate_reason else "unknown"
            _td_would_fail_reason[key] = _td_would_fail_reason.get(key, 0) + 1
    except Exception as e:
        print(f"  [WARN] validate_entry failed for {sym}: {e}")

# Confirm 0 orders placed (execute_buy/execute_short never called)
# In this script we never call execute_buy — the counter stays 0.
_orders_placed = 0

# ── 10. Check training_records for new entries ────────────────────────────────
_training_path = _REPO / "data" / "training_records.jsonl"
_training_count_before = 0
_training_count_after = 0
try:
    if _training_path.exists():
        with open(_training_path) as _tf:
            lines = _tf.readlines()
        _training_count_after = len(lines)
        # We don't have a "before" count here since this is a standalone run.
        # Report current count; if it changed between runs the difference is pollution.
except Exception:
    pass

# ── 11. Read shadow log for new records written during this scan ──────────────
_shadow_path = _REPO / "data" / "position_research_shadow.jsonl"
_shadow_records = 0
try:
    if _shadow_path.exists():
        with open(_shadow_path) as _sf:
            all_shadow = _sf.readlines()
        _shadow_records = len(all_shadow)
except Exception:
    pass

# ── 12. Print funnel report ───────────────────────────────────────────────────
_mode_label = "SYNTHETIC FALLBACK (safety-only — NOT valid for cap diagnosis)" if _synthetic_mode else "REAL SCORING DATA (valid for cap diagnosis)"
print("\n" + "=" * 70)
print(f"TIER D FUNNEL REPORT — {_mode_label}")
print("=" * 70)
print(f" 1. Tier D loaded (from PRU)               : {_td_loaded}")
print(f" 2. all_scored count (total symbols)        : {len(all_scored)}  {'[SYNTHETIC]' if _synthetic_mode else '[REAL]'}")
print(f"    Tier D in all_scored                    : {_td_all_scored}")
print(f" 3. Tier D passed strategy threshold        : {_td_passed_strategy}")
print(f" 4. Tier D rescued (by discovery/archetype) : {_td_rescued}")
print(f" 5. Tier D in pipeline output (signals)     : {_td_pipeline_output}")
print(f" 6. raw candidates before cap               : {len(_cut_all_sorted)}")
print(f"    raw_tier_d_before_cap                   : {len(_td_before_cap)}")
print(f" 7. selected_tier_d_after_cap               : {len(_td_after_cap)}")
print(f" 8. dropped_tier_d_by_cap                   : {len(_td_dropped_cap)}")
print(f"    dropped w/ discovery_score >= 6         : {sum(1 for c in _td_dropped_cap if (c.get('discovery_score') or 0) >= 6)}")
print(f"    dropped w/ archetypes                   : {sum(1 for c in _td_dropped_cap if c.get('matched_position_archetypes'))}")
print(f" 9. Tier D reaching Apex payload            : {len(_td_after_cap)}")
print(f"10. Tier D reaching dispatch                : {_td_reaching_validate}")
print(f"11. Tier D reaching validate_entry          : {_td_reaching_validate}")
print(f"12. Tier D shadow-blocked                   : {_td_shadow_blocked}")
print(f"13. Orders placed                           : {_orders_placed}  (MUST BE 0)")
print(f"14. training_records current count          : {_training_count_after}  (run twice to confirm no change)")
if _synthetic_mode:
    print("\n  [!] SYNTHETIC MODE: cap diagnosis is NOT valid. Re-run during market hours.")

# ── 13. Cap bottleneck verdict ────────────────────────────────────────────────
print("\n--- Cap Bottleneck Verdict ---")
if _synthetic_mode:
    print("  [INCONCLUSIVE — synthetic mode, cap not stressed, re-run during market hours]")
elif len(_td_before_cap) == 0:
    print("  raw_tier_d_before_cap = 0 → Tier D not reaching cap.")
    print("  Bottleneck is in pipeline stages 1-6 (scoring/threshold/rescue).")
    print("  Investigate: td_above_thresh, td_passed_strategy, td_rescued.")
elif not _synthetic_mode and len(_td_dropped_cap) > len(_td_after_cap):
    print(f"  DROPPED ({len(_td_dropped_cap)}) > SELECTED ({len(_td_after_cap)})")
    print("  → CAP IS PRIMARY BOTTLENECK for Tier D.")
    print(f"  max_tier_d_score_before_cap={_max_td_score}")
    print(f"  min_selected_score_after_cap={_min_sel_score}")
    if _cap_record["tier_d_with_archetypes_dropped"]:
        print("  [!] Tier D candidates WITH archetypes were dropped by cap.")
    if _cap_record["tier_d_strong_discovery_dropped"]:
        print("  [!] Tier D candidates with strong discovery score (≥6) were dropped by cap.")
elif not _synthetic_mode and len(_td_dropped_cap) > 0:
    print(f"  Partial cap impact: {len(_td_dropped_cap)} dropped, {len(_td_after_cap)} selected.")
    print(f"  max_tier_d_score={_max_td_score}, min_selected={_min_sel_score}")
elif not _synthetic_mode:
    print("  Cap is NOT dropping any Tier D candidates.")
    if _td_reaching_validate == 0:
        print("  But Tier D not reaching validate_entry. Check pipeline stages.")

# ── 14. Shadow mode confirmation ──────────────────────────────────────────────
print("\n--- Shadow Mode Confirmation ---")
print(f"  shadow_mode=True: {'YES' if _shadow_mode else 'NO — MISCONFIGURED'}")
print(f"  allow_live=False: {'YES' if not _allow_live else 'NO — MISCONFIGURED'}")
print(f"  orders_placed=0: {'YES' if _orders_placed == 0 else 'NO — INVESTIGATE'}")
print(f"  shadow_blocked: {_td_shadow_blocked}")
print(f"  would_have_passed (simulated): {_td_would_pass}")
if _td_would_fail_reason:
    print("  simulated_reject_reasons:")
    for reason, count in sorted(_td_would_fail_reason.items(), key=lambda x: -x[1]):
        print(f"    {count}x  {reason}")

# ── 15. Selected Tier D symbols ───────────────────────────────────────────────
if _td_after_cap:
    print("\n--- Tier D Selected After Cap ---")
    for c in _td_after_cap:
        print(f"  {c.get('symbol'):<8}  score={c.get('score', 0):5.1f}  "
              f"discovery={c.get('discovery_score', 'n/a')}  "
              f"archetypes={c.get('matched_position_archetypes', [])}")

# ── 16. Dropped Tier D symbols ────────────────────────────────────────────────
if _td_dropped_cap:
    print("\n--- Tier D Dropped by Cap (top 10) ---")
    for c in _td_dropped_cap[:10]:
        print(f"  {c.get('symbol'):<8}  score={c.get('score', 0):5.1f}  "
              f"discovery={c.get('discovery_score', 'n/a')}  "
              f"archetypes={c.get('matched_position_archetypes', [])}")

# ── 17. Run evidence report ───────────────────────────────────────────────────
print("\n" + "=" * 70)
print("Running tier_d_evidence_report.py Section 0b...")
print("=" * 70)
import subprocess
result = subprocess.run(
    [sys.executable, str(_REPO / "scripts" / "tier_d_evidence_report.py")],
    capture_output=False,
    text=True,
    cwd=str(_REPO),
)
if result.returncode != 0:
    print(f"[WARN] Evidence report exited with code {result.returncode}")

print("\n[DONE] Tier D test scan complete.")
