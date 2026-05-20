"""
scripts/signal_validation_report.py — Executed-trade signal validation.

Computes Spearman IC between each signal dimension and realized pnl_pct using
closed-trade records from training_records.jsonl.

THIS IS EXECUTION IC — computed on trades the system chose to enter.
It is subject to selection bias: dimensions that consistently drove entry
decisions appear more predictive than they truly are. The candidate-level IC
in signals_log.jsonl / factor_analysis.py is the unbiased pre-selection view.
Use this report to inform walk-forward calibration decisions, not to update
live weights directly.

pnl_pct convention: positive = trade was profitable regardless of LONG/SHORT.
No further direction transformation is applied.

Run:  python3 scripts/signal_validation_report.py
Output: data/signal_validation_report.json + stdout summary table
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

import training_store

_REPORT_FILE = _REPO / "data" / "signal_validation_report.json"
_IC_WEIGHTS_FILE = _REPO / "data" / "ic_weights.json"

MIN_N = 30            # minimum observations for IC to be interpretable
QUANTILE_BUCKETS = 5
IC_SIGNAL_THRESHOLD = 0.05   # IC > this + p < 0.05 → SIGNAL
IC_MARGINAL_THRESHOLD = 0.02

# Only 4 records exist for these — skip entirely
_NOISE_DIMS = frozenset({"fx_momentum", "fx_macro"})


# ── Eligibility ───────────────────────────────────────────────────────────────

def _load_eligible() -> tuple[list[dict], int]:
    all_records = training_store.load()
    eligible, n_excluded = [], 0
    for r in all_records:
        if r.get("ml_eligible") is False:
            n_excluded += 1
        else:
            eligible.append(r)
    return eligible, n_excluded


def _get_scores(record: dict) -> dict[str, float]:
    """Merge score_breakdown (wins) over signal_scores; skip noise dimensions."""
    ss = {k: float(v) for k, v in (record.get("signal_scores") or {}).items()
          if k not in _NOISE_DIMS}
    sb = {k: float(v) for k, v in (record.get("score_breakdown") or {}).items()
          if k not in _NOISE_DIMS}
    return {**ss, **sb}  # score_breakdown wins on conflict


def _build_usable(eligible: list[dict]) -> list[dict]:
    return [r for r in eligible
            if r.get("pnl_pct") is not None and _get_scores(r)]


def _collect_dims(usable: list[dict]) -> list[str]:
    seen: set[str] = set()
    for r in usable:
        seen.update(_get_scores(r).keys())
    return sorted(seen - _NOISE_DIMS)


# ── IC math ───────────────────────────────────────────────────────────────────

def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    """Pure numpy Spearman rank correlation — no scipy dependency."""
    n = len(x)
    if n < 3:
        return 0.0
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    d = rx - ry
    denom = n * (n * n - 1)
    return float(1.0 - 6.0 * np.sum(d * d) / denom) if denom > 0 else 0.0


def _p_value(ic: float, n: int) -> float | None:
    if n < 4 or not np.isfinite(ic):
        return None
    try:
        from scipy.stats import t as t_dist
        t_stat = float(ic) * np.sqrt((n - 2) / max(1.0 - float(ic) ** 2, 1e-9))
        return float(2 * t_dist.sf(abs(t_stat), df=n - 2))
    except ImportError:
        return None


def _fisher_ci(ic: float, n: int) -> tuple[float, float] | tuple[None, None]:
    if n < 4 or not np.isfinite(ic):
        return None, None
    z = np.arctanh(np.clip(float(ic), -0.9999, 0.9999))
    se = 1.0 / np.sqrt(n - 3)
    return (round(float(np.tanh(z - 1.96 * se)), 4),
            round(float(np.tanh(z + 1.96 * se)), 4))


def _assign_flag(ic: float | None, p: float | None, n: int) -> str:
    if n < MIN_N:
        return "INSUFFICIENT_EVIDENCE"
    if ic is None or not np.isfinite(ic):
        return "ZERO"
    if ic > IC_SIGNAL_THRESHOLD and p is not None and p < 0.05:
        return "SIGNAL"
    if ic > IC_MARGINAL_THRESHOLD:
        return "MARGINAL"
    if ic < 0:
        return "NEGATIVE"
    return "NOISE"


def _dim_ic(pairs: list[tuple[float, float]]) -> dict:
    n = len(pairs)
    if n == 0:
        return {"ic": None, "p_value": None, "ci_lo": None, "ci_hi": None,
                "n": 0, "flag": "INSUFFICIENT_EVIDENCE"}
    scores = np.array([p[0] for p in pairs])
    outcomes = np.array([p[1] for p in pairs])
    if np.std(scores) < 1e-9:
        ci_lo, ci_hi = _fisher_ci(0.0, n)
        return {"ic": 0.0, "p_value": 1.0, "ci_lo": ci_lo, "ci_hi": ci_hi,
                "n": n, "flag": _assign_flag(0.0, 1.0, n)}
    ic = _spearman(scores, outcomes)
    p = _p_value(ic, n)
    ci_lo, ci_hi = _fisher_ci(ic, n)
    return {"ic": round(ic, 4), "p_value": round(p, 4) if p is not None else None,
            "ci_lo": ci_lo, "ci_hi": ci_hi, "n": n, "flag": _assign_flag(ic, p, n)}


def _compute_all_dims(usable: list[dict], dims: list[str]) -> dict[str, dict]:
    results: dict[str, dict] = {}
    for dim in dims:
        pairs = [(scores[dim], r["pnl_pct"])
                 for r in usable
                 if dim in (scores := _get_scores(r))]
        results[dim] = _dim_ic(pairs)
    return results


# ── Quantile returns ──────────────────────────────────────────────────────────

def _quantile_returns(usable: list[dict], dim: str) -> list[dict] | None:
    pairs = [(scores[dim], r["pnl_pct"])
             for r in usable
             if dim in (scores := _get_scores(r))]
    if len(pairs) < MIN_N:
        return None
    sc = np.array([p[0] for p in pairs])
    ret = np.array([p[1] for p in pairs])
    cuts = np.percentile(sc, np.linspace(0, 100, QUANTILE_BUCKETS + 1))
    buckets = []
    for i in range(QUANTILE_BUCKETS):
        lo, hi = cuts[i], cuts[i + 1]
        mask = (sc >= lo) & (sc <= hi) if i == QUANTILE_BUCKETS - 1 else (sc >= lo) & (sc < hi)
        if not mask.any():
            continue
        buckets.append({
            "bucket": i + 1,
            "score_range": [round(float(lo), 2), round(float(hi), 2)],
            "mean_score": round(float(sc[mask].mean()), 2),
            "mean_pnl_pct": round(float(ret[mask].mean()), 4),
            "n": int(mask.sum()),
        })
    return buckets


# ── Group-level IC (hold-time, trade-type, regime) ────────────────────────────

def _ic_by_group(usable: list[dict], key_fn, dims: list[str],
                 min_n: int = MIN_N) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in usable:
        groups[key_fn(r)].append(r)
    out: dict[str, dict] = {}
    for name, recs in sorted(groups.items()):
        dim_results: dict[str, dict] = {}
        for dim in dims:
            pairs = [(scores[dim], r["pnl_pct"])
                     for r in recs
                     if dim in (scores := _get_scores(r))]
            result = _dim_ic(pairs)
            if len(pairs) < min_n:
                result["flag"] = "INSUFFICIENT"
            dim_results[dim] = result
        out[str(name)] = {"n": len(recs), "dims": dim_results}
    return out


def _hold_bucket(r: dict) -> str:
    h = r.get("hold_minutes") or 0
    if h < 60:
        return "scalp_lt60min"
    if h < 480:
        return "medium_60to480min"
    return "swing_gte480min"


# ── Candidate vs execution IC comparison ─────────────────────────────────────

def _load_candidate_ic() -> dict:
    if not _IC_WEIGHTS_FILE.exists():
        return {}
    try:
        with open(_IC_WEIGHTS_FILE) as f:
            return json.load(f).get("raw_ic", {})
    except Exception:
        return {}


def _build_comparison(dims: list[str], dim_results: dict, candidate_ic: dict) -> dict:
    comparison: dict[str, dict] = {}
    for dim in dims:
        cand = candidate_ic.get(dim)
        exec_ic = dim_results.get(dim, {}).get("ic")
        diverge = None
        if cand is not None and exec_ic is not None:
            if (cand > 0) != (exec_ic > 0):
                diverge = "SIGN_FLIP"
            elif abs(cand - exec_ic) > 0.10:
                diverge = "LARGE_GAP"
        comparison[dim] = {
            "candidate_ic": round(cand, 4) if cand is not None else None,
            "execution_ic": exec_ic,
            "divergence": diverge,
        }
    return {
        "note": (
            "Directional comparison only — not apples-to-apples. "
            "Candidate IC: signals_log + price forward returns (all scanned candidates). "
            "Execution IC: training_records + realized pnl_pct (executed trades only, "
            "selection bias applies)."
        ),
        "dims": comparison,
    }


# ── Recommendations ───────────────────────────────────────────────────────────

def _build_recommendations(dim_results: dict) -> dict:
    walk_forward, flag_review, inactive, insufficient = [], [], [], []
    for dim, r in dim_results.items():
        flag = r.get("flag", "")
        ic = r.get("ic")
        n = r.get("n", 0)
        if flag == "SIGNAL":
            walk_forward.append(dim)
        elif flag == "NEGATIVE" and n >= MIN_N:
            flag_review.append(dim)
        elif flag == "ZERO" or (ic is not None and np.isfinite(ic) and ic == 0.0 and n >= MIN_N):
            inactive.append(dim)
        elif flag == "INSUFFICIENT_EVIDENCE":
            insufficient.append(dim)
    return {
        "candidate_for_walk_forward": sorted(walk_forward),
        "flag_for_review": sorted(flag_review),
        "inactive_skip": sorted(inactive),
        "insufficient_evidence": sorted(insufficient),
    }


# ── Report assembly ───────────────────────────────────────────────────────────

def _build_meta(eligible: list[dict], n_excluded: int, usable: list[dict],
                dims: list[str]) -> dict:
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "source_file": "data/training_records.jsonl",
        "total_records": n_excluded + len(eligible),
        "eligible_records": len(eligible),
        "ineligible_excluded": n_excluded,
        "usable_records": len(usable),
        "eligible_missing_scores": len(eligible) - len(usable),
        "dimensions_tested": dims,
        "min_n_for_ic": MIN_N,
        "quantile_buckets": QUANTILE_BUCKETS,
        "pnl_pct_convention": (
            "direction-adjusted: positive = trade profitable regardless of LONG/SHORT. "
            "LONG: pnl_pct = (exit - fill) / fill. "
            "SHORT: pnl_pct = (fill - exit) / fill. No additional transformation applied."
        ),
        "selection_bias_warning": (
            "EXECUTION IC — computed on trades the system chose to enter. "
            "Dimensions that consistently drove entry selection appear more predictive than they are. "
            "Candidate-level IC (signals_log.jsonl / factor_analysis.py) is the unbiased view. "
            "Use this report to guide walk-forward calibration, NOT to directly update live weights."
        ),
    }


# ── Stdout printing ───────────────────────────────────────────────────────────

def _print_ic_table(dim_ic: dict) -> None:
    print(f"\n  {'DIMENSION':<22} {'N':>5} {'IC':>8} {'p-val':>7} {'95% CI':>18}  FLAG")
    print("  " + "-" * 74)
    for dim, r in sorted(dim_ic.items()):
        ic = r["ic"]
        p = r["p_value"]
        n = r["n"]
        ci_lo, ci_hi = r["ci_lo"], r["ci_hi"]
        ic_s = f"{ic:+.4f}" if ic is not None else "    N/A"
        p_s = f"{p:.4f}" if p is not None else "  N/A"
        ci_s = f"[{ci_lo:+.3f},{ci_hi:+.3f}]" if ci_lo is not None else "       N/A"
        print(f"  {dim:<22} {n:>5} {ic_s:>8} {p_s:>7} {ci_s:>18}  {r['flag']}")


def _print_comparison(comparison: dict) -> None:
    print(f"\n  {'DIMENSION':<22} {'CANDIDATE':>10} {'EXECUTION':>10}  DIVERGENCE")
    print("  " + "-" * 56)
    for dim, c in sorted(comparison["dims"].items()):
        cand = c["candidate_ic"]
        exec_ic = c["execution_ic"]
        cand_s = f"{cand:+.4f}" if cand is not None else "    N/A"
        exec_s = f"{exec_ic:+.4f}" if exec_ic is not None else "    N/A"
        div = c["divergence"] or ""
        print(f"  {dim:<22} {cand_s:>10} {exec_s:>10}  {div}")


def _print_recommendations(recs: dict) -> None:
    print(f"\n  WALK-FORWARD CANDIDATES : {', '.join(recs['candidate_for_walk_forward']) or 'none'}")
    print(f"  FLAG FOR REVIEW         : {', '.join(recs['flag_for_review']) or 'none'}")
    print(f"  INACTIVE (all-zero)     : {', '.join(recs['inactive_skip']) or 'none'}")
    print(f"  INSUFFICIENT EVIDENCE   : {', '.join(recs['insufficient_evidence']) or 'none'}")


def _print_summary(report: dict) -> None:
    m = report["meta"]
    print()
    print("=" * 76)
    print("  DECIFER — EXECUTED-TRADE SIGNAL VALIDATION REPORT")
    print(f"  Generated : {m['generated_at'][:19]}")
    print(f"  Source    : {m['source_file']}")
    print("=" * 76)
    print(f"  Total={m['total_records']}  Eligible={m['eligible_records']}  "
          f"Excluded={m['ineligible_excluded']}  "
          f"Usable={m['usable_records']}  MissingScores={m['eligible_missing_scores']}")
    print()
    print("  *** SELECTION BIAS WARNING ***")
    print("  EXECUTION IC only. Candidate IC (factor_analysis.py) is the cleaner view.")
    print("  Do not use this report to update live weights directly.")

    _print_ic_table(report["dim_ic"])
    _print_recommendations(report["recommendations"])

    print("\n  Candidate IC vs Execution IC (directional comparison — not apples-to-apples):")
    _print_comparison(report["candidate_vs_execution_ic"])

    print(f"\n  Full report → {_REPORT_FILE}")
    print("=" * 76)
    print()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    print("Loading eligible training records ...")
    eligible, n_excluded = _load_eligible()
    print(f"  Eligible={len(eligible)}  Excluded(ml_eligible=False)={n_excluded}")

    usable = _build_usable(eligible)
    print(f"  Usable (has scores + pnl_pct): {len(usable)}")

    dims = _collect_dims(usable)
    print(f"  Dimensions: {dims}")

    print("Computing per-dimension IC ...")
    dim_results = _compute_all_dims(usable, dims)

    print("Computing quantile returns ...")
    quantiles = {dim: q for dim in dims
                 if (q := _quantile_returns(usable, dim)) is not None}

    print("Computing stratifications ...")
    hold_strat = _ic_by_group(usable, _hold_bucket, dims, min_n=20)
    trade_type_strat = _ic_by_group(
        usable, lambda r: r.get("trade_type") or "UNKNOWN", dims, min_n=20)
    regime_strat = _ic_by_group(
        usable, lambda r: r.get("regime") or "UNKNOWN", dims, min_n=MIN_N)

    candidate_ic = _load_candidate_ic()

    report = {
        "meta": _build_meta(eligible, n_excluded, usable, dims),
        "dim_ic": dim_results,
        "quantile_returns": quantiles,
        "hold_time_stratification": hold_strat,
        "trade_type_stratification": trade_type_strat,
        "regime_stratification": regime_strat,
        "candidate_vs_execution_ic": _build_comparison(dims, dim_results, candidate_ic),
        "recommendations": _build_recommendations(dim_results),
    }

    _REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_REPORT_FILE, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport written to {_REPORT_FILE}")

    _print_summary(report)


if __name__ == "__main__":
    main()
