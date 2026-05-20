"""
scripts/walkforward_calibration_report.py — Walk-forward weight calibration report.

Primary source : candidate IC from ic_weights.json / ic_weights_live_history.jsonl
                 (36k+ scanned candidates, no selection bias)
Advisory source: executed-trade IC from signal_validation_report.json
                 (177 trades, selection-biased, advisory only)

Proposes calibrated weights for each signal dimension.
Does NOT write to ic_weights.json or any live scoring file.
Output is a proposal only — activation requires explicit Amit approval.

Calibration rules (locked)
--------------------------
1. Candidate IC is the primary weight derivation signal.
2. Execution IC is advisory — may cap or flag, must not increase any weight
   above the candidate-IC-derived level.
3. overnight_drift: BLOCKED CRITICAL — negative in both sources, statistically
   significant in execution IC (p=0.009). Weight locked at 0.
4. Sign-flip (candidate positive, execution negative, not significant p≥0.05):
   FLAG for review, preserve candidate weight unchanged.
5. Sign-flip (candidate positive, execution negative, significant p<0.05):
   CAP proposed weight at BASELINE_WEIGHTS[dim].
6. Inactive (both sources zero): weight = 0, excluded from calibration.

Run:  python3 scripts/walkforward_calibration_report.py
Output: data/proposed_calibrated_weights.json + stdout summary
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from ic.constants import BASELINE_WEIGHTS, DIMENSIONS
from ic.core import normalize_ic_weights

_CANDIDATE_IC_FILE = _REPO / "data" / "ic_weights.json"
_EXEC_IC_FILE = _REPO / "data" / "signal_validation_report.json"
_IC_HISTORY_FILE = _REPO / "data" / "ic_weights_live_history.jsonl"
_PROPOSED_FILE = _REPO / "data" / "proposed_calibrated_weights.json"

EXEC_SIG_P = 0.05   # p below this → execution IC is statistically significant
MIN_EXEC_N = 30     # minimum execution records for advisory cap to apply

# ── Classification labels ─────────────────────────────────────────────────────
C_INACTIVE = "INACTIVE"
C_BLOCKED_CRITICAL = "BLOCKED_NEGATIVE_BOTH_CRITICAL"
C_NEGATIVE_BOTH = "NEGATIVE_BOTH_SOURCES"
C_SIGN_FLIP_EXEC_NEG = "SIGN_FLIP_EXEC_NEGATIVE"
C_SIGN_FLIP_EXEC_NEG_SIG = "SIGN_FLIP_EXEC_NEGATIVE_SIGNIFICANT"
C_SIGN_FLIP_EXEC_POS = "SIGN_FLIP_EXEC_POSITIVE_CAND_NEGATIVE"
C_CONFIRMED = "CONFIRMED_POSITIVE_BOTH"
C_CANDIDATE_ONLY = "CANDIDATE_POSITIVE_EXEC_NEUTRAL"


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_candidate_ic() -> dict:
    with open(_CANDIDATE_IC_FILE) as f:
        return json.load(f)


def _load_execution_ic() -> dict[str, dict]:
    if not _EXEC_IC_FILE.exists():
        return {}
    with open(_EXEC_IC_FILE) as f:
        report = json.load(f)
    return report.get("dim_ic", {})


def _load_ic_history() -> dict[str, dict]:
    """Return per-dimension stability metrics across IC history entries."""
    if not _IC_HISTORY_FILE.exists():
        return {}
    entries = []
    with open(_IC_HISTORY_FILE) as f:
        for line in f:
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return _compute_stability(entries)


def _compute_stability(entries: list[dict]) -> dict[str, dict]:
    stability: dict[str, dict] = {}
    for dim in DIMENSIONS:
        vals = [e["raw_ic"][dim] for e in entries
                if "raw_ic" in e and dim in e["raw_ic"]
                and e["raw_ic"][dim] is not None]
        if not vals:
            stability[dim] = {"mean_ic": None, "std_ic": None,
                              "sign_pct": None, "n_obs": 0}
            continue
        mean = float(np.mean(vals))
        std = float(np.std(vals)) if len(vals) > 1 else 0.0
        n_pos = sum(1 for v in vals if v > 0)
        n_neg = sum(1 for v in vals if v < 0)
        sign_pct = round(max(n_pos, n_neg) / len(vals) * 100, 1)
        stability[dim] = {"mean_ic": round(mean, 4), "std_ic": round(std, 4),
                          "sign_pct": sign_pct, "n_obs": len(vals)}
    return stability


# ── Classification ────────────────────────────────────────────────────────────

def _classify(dim: str, cand_ic: float,
              exec_ic: float | None, exec_p: float | None,
              exec_n: int) -> str:
    is_exec_sig = (exec_p is not None and exec_p < EXEC_SIG_P
                   and exec_n >= MIN_EXEC_N)
    # Zero candidate IC — check if exec also zero
    if cand_ic == 0.0:
        if exec_ic is None or exec_ic == 0.0:
            return C_INACTIVE
    # Negative or zero candidate IC
    if cand_ic <= 0.0:
        if exec_ic is not None and exec_ic < 0.0 and is_exec_sig:
            return C_BLOCKED_CRITICAL
        if exec_ic is not None and exec_ic > 0.0:
            return C_SIGN_FLIP_EXEC_POS
        return C_NEGATIVE_BOTH
    # Positive candidate IC
    if exec_ic is None or exec_ic == 0.0:
        return C_CANDIDATE_ONLY
    if exec_ic > 0.0:
        return C_CONFIRMED
    # exec_ic < 0
    if is_exec_sig:
        return C_SIGN_FLIP_EXEC_NEG_SIG
    return C_SIGN_FLIP_EXEC_NEG


# ── Advisory weight adjustment ────────────────────────────────────────────────

def _advisory_weight(classification: str, cand_weight: float,
                     dim: str, exec_p: float | None) -> tuple[float, str]:
    """
    Apply execution-IC advisory rules to candidate-derived weight.
    Returns (proposed_weight, advisory_action_taken).
    Execution IC may only cap or block — never increase a weight.
    """
    if classification in (C_INACTIVE, C_NEGATIVE_BOTH,
                          C_BLOCKED_CRITICAL, C_SIGN_FLIP_EXEC_POS):
        return 0.0, "CONFIRMED_ZERO"

    if classification == C_SIGN_FLIP_EXEC_NEG_SIG:
        cap = BASELINE_WEIGHTS.get(dim, 0.0)
        if cand_weight > cap:
            return cap, f"CAPPED_AT_BASELINE_{cap:.3f}"
        return cand_weight, "BELOW_BASELINE_NO_CAP_NEEDED"

    # SIGN_FLIP_EXEC_NEG (not sig), CONFIRMED, CANDIDATE_ONLY
    return cand_weight, "PRESERVED_CANDIDATE_WEIGHT"


# ── Per-dimension report entry ────────────────────────────────────────────────

def _dim_entry(dim: str, cand_ic: float, cand_weight: float,
               exec_dim: dict, hist: dict, classification: str,
               proposed_weight: float, advisory_action: str) -> dict:
    exec_ic = exec_dim.get("ic")
    exec_p = exec_dim.get("p_value")
    exec_n = exec_dim.get("n", 0)
    return {
        "candidate_ic": round(cand_ic, 4),
        "candidate_weight": round(cand_weight, 4),
        "execution_ic": exec_ic,
        "execution_p_value": exec_p,
        "execution_n": exec_n,
        "execution_flag": exec_dim.get("flag"),
        "sign_agreement": (
            None if exec_ic is None
            else (cand_ic >= 0) == (exec_ic >= 0)
        ),
        "history_mean_ic": hist.get("mean_ic"),
        "history_std_ic": hist.get("std_ic"),
        "history_sign_pct": hist.get("sign_pct"),
        "history_n_obs": hist.get("n_obs", 0),
        "classification": classification,
        "proposed_weight": round(proposed_weight, 4),
        "weight_delta": round(proposed_weight - cand_weight, 4),
        "advisory_action": advisory_action,
    }


# ── Post-advisory renormalization ─────────────────────────────────────────────

def _renormalize(proposed: dict[str, float]) -> dict[str, float]:
    """Renormalize proposed weights to sum to 1.0 after advisory caps."""
    total = sum(proposed.values())
    if total < 1e-9:
        return dict(proposed)
    return {d: round(w / total, 6) for d, w in proposed.items()}


# ── Recommendations ───────────────────────────────────────────────────────────

def _recommendations(dim_entries: dict[str, dict]) -> dict:
    confirmed, flagged, blocked, inactive, capped = [], [], [], [], []
    for dim, e in dim_entries.items():
        c = e["classification"]
        if c == C_INACTIVE:
            inactive.append(dim)
        elif c == C_BLOCKED_CRITICAL:
            blocked.append(dim)
        elif c in (C_SIGN_FLIP_EXEC_NEG, C_SIGN_FLIP_EXEC_NEG_SIG):
            flagged.append(dim)
        elif c == C_SIGN_FLIP_EXEC_NEG_SIG:
            capped.append(dim)
        elif c in (C_CONFIRMED, C_CANDIDATE_ONLY):
            confirmed.append(dim)
    return {
        "confirmed_positive": sorted(confirmed),
        "flagged_sign_flip": sorted(flagged),
        "blocked_critical_negative": sorted(blocked),
        "inactive_excluded": sorted(inactive),
        "weight_capped_by_advisory": sorted(capped),
    }


# ── Summary printing ──────────────────────────────────────────────────────────

def _print_dim_table(dim_entries: dict[str, dict]) -> None:
    hdr = f"  {'DIM':<22} {'CAND_IC':>8} {'CAND_WT':>8} {'EXEC_IC':>8} {'EXEC_P':>7} {'PROP_WT':>8}  CLASSIFICATION"
    print(hdr)
    print("  " + "-" * 90)
    for dim, e in sorted(dim_entries.items()):
        cic = f"{e['candidate_ic']:+.4f}"
        cwt = f"{e['candidate_weight']:.4f}"
        eic = f"{e['execution_ic']:+.4f}" if e['execution_ic'] is not None else "   N/A"
        ep = f"{e['execution_p_value']:.3f}" if e['execution_p_value'] is not None else "  N/A"
        pwt = f"{e['proposed_weight']:.4f}"
        cls = e['classification']
        print(f"  {dim:<22} {cic:>8} {cwt:>8} {eic:>8} {ep:>7} {pwt:>8}  {cls}")


def _print_summary(report: dict) -> None:
    m = report["meta"]
    print()
    print("=" * 92)
    print("  DECIFER — WALK-FORWARD WEIGHT CALIBRATION REPORT")
    print(f"  Generated : {m['generated_at'][:19]}  |  "
          f"Candidate IC records: {m['candidate_n_records']}  |  "
          f"Execution IC trades: {m['execution_n_records']}")
    print("=" * 92)
    print()
    print("  *** PROPOSAL ONLY — NOT ACTIVE. Weights require explicit Amit approval. ***")
    print("  Candidate IC is PRIMARY. Execution IC is ADVISORY (cap/flag only, no increases).")
    print()
    _print_dim_table(report["dim_calibration"])
    r = report["recommendations"]
    print()
    print(f"  CONFIRMED POSITIVE    : {', '.join(r['confirmed_positive']) or 'none'}")
    print(f"  FLAGGED SIGN-FLIP     : {', '.join(r['flagged_sign_flip']) or 'none'}")
    print(f"  BLOCKED CRITICAL      : {', '.join(r['blocked_critical_negative']) or 'none'}")
    print(f"  INACTIVE EXCLUDED     : {', '.join(r['inactive_excluded']) or 'none'}")
    if r['weight_capped_by_advisory']:
        print(f"  WEIGHT CAPPED (exec)  : {', '.join(r['weight_capped_by_advisory'])}")
    print()
    total = sum(report["proposed_weights"].values())
    print(f"  Proposed weight sum: {total:.6f}  (should be 1.0)")
    no_change = all(abs(e["weight_delta"]) < 1e-6
                    for e in report["dim_calibration"].values())
    if no_change:
        print("  Weight delta: NONE — proposed weights identical to candidate IC weights.")
        print("  This is correct: no execution IC result is strong enough to require adjustment.")
    else:
        for dim, e in sorted(report["dim_calibration"].items()):
            if abs(e["weight_delta"]) > 1e-6:
                print(f"  Weight change: {dim}: {e['candidate_weight']:.4f} → {e['proposed_weight']:.4f}")
    print()
    print(f"  Full proposal → {_PROPOSED_FILE}")
    print("=" * 92)
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("Loading candidate IC ...")
    candidate = _load_candidate_ic()
    cand_raw_ic: dict[str, float] = candidate.get("raw_ic", {})
    cand_weights: dict[str, float] = candidate.get("weights", {})

    print("Running normalize_ic_weights on candidate raw_ic ...")
    proposed_weights_dict, norm_meta = normalize_ic_weights(cand_raw_ic)

    print("Loading execution IC (advisory) ...")
    exec_ic = _load_execution_ic()

    print("Loading IC history for stability analysis ...")
    history = _load_ic_history()

    print("Classifying dimensions ...")
    dim_calibration: dict[str, dict] = {}
    proposed: dict[str, float] = {}

    for dim in DIMENSIONS:
        cic = cand_raw_ic.get(dim, 0.0)
        cwt = proposed_weights_dict.get(dim, 0.0)
        exec_dim = exec_ic.get(dim, {})
        eic = exec_dim.get("ic")
        ep = exec_dim.get("p_value")
        en = exec_dim.get("n", 0)
        hist = history.get(dim, {})

        classification = _classify(dim, cic, eic, ep, en)
        pwt, action = _advisory_weight(classification, cwt, dim, ep)
        proposed[dim] = pwt

        dim_calibration[dim] = _dim_entry(
            dim, cic, cwt, exec_dim, hist, classification, pwt, action)

    proposed_norm = _renormalize(proposed)
    for dim in DIMENSIONS:
        dim_calibration[dim]["proposed_weight"] = round(proposed_norm.get(dim, 0.0), 4)
        dim_calibration[dim]["weight_delta"] = round(
            proposed_norm.get(dim, 0.0) - cand_weights.get(dim, 0.0), 4)

    report = {
        "meta": {
            "generated_at": datetime.now(UTC).isoformat(),
            "activation_status": "PROPOSAL_ONLY — NOT ACTIVE. Requires explicit Amit approval.",
            "candidate_ic_file": "data/ic_weights.json",
            "execution_ic_file": "data/signal_validation_report.json",
            "candidate_n_records": candidate.get("n_records", 0),
            "candidate_n_dates": candidate.get("n_independent_dates", 0),
            "candidate_advisory_only": candidate.get("advisory_only", True),
            "execution_n_records": (exec_ic.get("trend", {}).get("n", 0)
                                    if exec_ic else 0),
            "dimensions_calibrated": DIMENSIONS,
            "norm_meta": norm_meta,
            "calibration_rules": {
                "primary_source": "candidate_ic",
                "advisory_source": "execution_ic",
                "exec_ic_role": (
                    "Advisory only. Execution IC may cap or flag, "
                    "but must not increase any weight above candidate-IC level."
                ),
                "overnight_drift_note": (
                    "Flagged CRITICAL: negative in both sources, "
                    "statistically significant in execution (p=0.009). Blocked at 0."
                ),
            },
        },
        "dim_calibration": dim_calibration,
        "proposed_weights": proposed_norm,
        "current_weights": cand_weights,
        "recommendations": _recommendations(dim_calibration),
    }

    _PROPOSED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_PROPOSED_FILE, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nProposed weights written to {_PROPOSED_FILE}")

    _print_summary(report)


if __name__ == "__main__":
    main()
