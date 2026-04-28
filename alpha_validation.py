#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  alpha_validation.py                       ║
# ║   Per-dimension IC analysis. Two data sources:              ║
# ║                                                              ║
# ║   1. Live IC  — ic_weights.json (Decifer 3.0 traded signals)║
# ║   2. Historical IC — signals_log_historical.jsonl           ║
# ║      (old era, good for basic technical dims only)          ║
# ║                                                              ║
# ║   Usage:                                                     ║
# ║     python alpha_validation.py           — print + save     ║
# ║     python alpha_validation.py --no-save — print only       ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import UTC, datetime

import numpy as np
import pandas as pd

log = logging.getLogger("decifer.alpha_validation")

_BASE = os.path.dirname(os.path.abspath(__file__))
_HIST_FILE = os.path.join(_BASE, "data", "signals_log_historical.jsonl")
_LIVE_IC_FILE = os.path.join(_BASE, "data", "ic_weights.json")
_TRAINING_FILE = os.path.join(_BASE, "data", "training_records.jsonl")
_OUT_FILE = os.path.join(_BASE, "data", "alpha_validation_report.json")
_CHIEF_OUT = os.path.join(_BASE, "chief-decifer", "state", "research", "research-alpha-validation.json")

# Dims present in historical file (old era — news/social/iv_skew/mtf were inactive)
_HIST_DIMS = ["trend", "momentum", "squeeze", "flow", "breakout", "reversion"]

_IC_KEEP = 0.05
_IC_REDUCE = 0.02
_PVAL_THRESHOLD = 0.05
_CLIP_SIGMA = 3.0
_ROLLING_WINDOWS = 40
_MAX_HIST_RECORDS = 600_000


# ── Data loading ───────────────────────────────────────────────────────────────


def _load_live_ic(path: str = _LIVE_IC_FILE) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def _load_historical(path: str = _HIST_FILE, max_records: int = _MAX_HIST_RECORDS) -> pd.DataFrame:
    rows = []
    with open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            sb = r.get("score_breakdown")
            fwd = r.get("fwd_return")
            direction = r.get("direction", "LONG")
            if not sb or fwd is None:
                continue
            row = {d: sb.get(d, 0) for d in _HIST_DIMS}
            # Direction-adjust: positive = good outcome for both LONG and SHORT
            row["fwd_return"] = float(fwd) * (-1.0 if direction == "SHORT" else 1.0)
            rows.append(row)
            if len(rows) >= max_records:
                break
    return pd.DataFrame(rows)


def _load_training(path: str = _TRAINING_FILE) -> pd.DataFrame:
    rows = []
    with open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            ss = r.get("signal_scores", {})
            pnl_pct = r.get("pnl_pct")
            if not ss or pnl_pct is None:
                continue
            rows.append({**ss, "fwd_return": float(pnl_pct)})
    return pd.DataFrame(rows)


# ── Core analytics ─────────────────────────────────────────────────────────────


def _spearman(x: np.ndarray, y: np.ndarray) -> tuple[float | None, float | None]:
    """Spearman rank correlation + two-tailed p-value, no scipy dependency.

    Uses Pearson correlation on ranks (equivalent to Spearman). Returns
    (None, None) if either input is constant or too short.
    """
    rx = pd.Series(x).rank(method="average").to_numpy()
    ry = pd.Series(y).rank(method="average").to_numpy()
    n = len(rx)
    rx_c = rx - rx.mean()
    ry_c = ry - ry.mean()
    denom = np.sqrt((rx_c ** 2).sum() * (ry_c ** 2).sum())
    if denom < 1e-10:
        return None, None
    ic = float((rx_c * ry_c).sum() / denom)
    if np.isnan(ic):
        return None, None
    t = ic * np.sqrt((n - 2) / max(1 - ic ** 2, 1e-10))
    # Two-tailed p-value via t-distribution; fall back to None if unavailable.
    try:
        from scipy.stats import t as t_dist
        p = float(2 * (1 - t_dist.cdf(abs(t), df=n - 2)))
    except Exception:
        p = None
    return ic, p


def _clip_outliers(series: pd.Series, sigma: float = _CLIP_SIGMA) -> pd.Series:
    mu, std = series.mean(), series.std()
    return series.clip(mu - sigma * std, mu + sigma * std)


def _ic_stats(scores: pd.Series, returns: pd.Series) -> dict:
    mask = scores.notna() & returns.notna() & (scores != 0)
    s, r = scores[mask], returns[mask]
    n = int(len(s))
    if n < 20:
        return {"ic": None, "tstat": None, "pvalue": None, "n": n}
    ic, pvalue = _spearman(s.to_numpy(), r.to_numpy())
    if ic is None:
        return {"ic": None, "tstat": None, "pvalue": None, "n": n}
    tstat = ic * np.sqrt((n - 2) / max(1 - ic ** 2, 1e-10))
    return {
        "ic": round(ic, 4),
        "tstat": round(tstat, 3),
        "pvalue": round(pvalue, 4) if pvalue is not None else None,
        "n": n,
    }


def _rolling_stability(df: pd.DataFrame, dim: str, n_windows: int = _ROLLING_WINDOWS) -> float | None:
    window_size = len(df) // n_windows
    if window_size < 50:
        return None
    ics = []
    for i in range(n_windows):
        chunk = df.iloc[i * window_size:(i + 1) * window_size]
        s, r = chunk[dim], chunk["fwd_return"]
        mask = s.notna() & r.notna() & (s != 0)
        if mask.sum() < 20:
            continue
        ic, _ = _spearman(s[mask].to_numpy(), r[mask].to_numpy())
        if ic is not None:
            ics.append(ic)
    return round(float(np.std(ics)), 4) if len(ics) >= 5 else None


def _quintile_spread(df: pd.DataFrame, dim: str) -> float | None:
    if dim not in df.columns:
        return None
    col = df[dim]
    mask = col.notna() & col.ne(0)
    if mask.sum() < 100:
        return None
    try:
        bins = pd.qcut(col[mask], q=5, duplicates="drop")
        q_returns = df.loc[mask, "fwd_return"].groupby(bins).mean()
        if len(q_returns) < 3:
            return None
        return round(float(q_returns.iloc[-1] - q_returns.iloc[0]), 6)
    except Exception:
        return None


def _verdict(ic: float | None, pvalue: float | None, n: int) -> str:
    if ic is None or n < 20:
        return "INSUFFICIENT_DATA"
    if abs(ic) < _IC_REDUCE:
        return "REMOVE"
    # Negative IC on a long-only signal is still a signal (short it or remove it)
    if ic < 0:
        return "NEGATIVE_IC"
    if ic >= _IC_KEEP and (pvalue is None or pvalue < _PVAL_THRESHOLD):
        return "KEEP"
    return "REDUCE_WEIGHT"


# ── Main pipeline ──────────────────────────────────────────────────────────────


def run_alpha_validation() -> dict:
    # ── Source 1: Live IC from ic_weights.json (Decifer 3.0) ──────────────────
    log.info("Loading live IC from ic_weights.json...")
    live_ic_cache = _load_live_ic()
    raw_ic = live_ic_cache.get("raw_ic", {})
    live_n = int(live_ic_cache.get("n_records", 0))
    live_updated = live_ic_cache.get("updated", "unknown")

    live_dims = {}
    for dim, ic_val in raw_ic.items():
        ic = float(ic_val) if ic_val is not None else None
        verdict = _verdict(ic, None, live_n)  # no per-dim p-value in cache
        live_dims[dim] = {
            "ic": round(ic, 4) if ic is not None else None,
            "tstat": None,
            "pvalue": None,
            "n": live_n,
            "ic_stability_std": None,
            "quintile_spread": None,
            "source": "live_ic_cache",
            "verdict": verdict,
        }

    # ── Source 2: Historical log (basic technical dims, 600k records) ─────────
    log.info("Loading historical signal records (up to %s)...", f"{_MAX_HIST_RECORDS:,}")
    hist = _load_historical()
    hist["fwd_return"] = _clip_outliers(hist["fwd_return"])
    log.info("Loaded %s historical records", f"{len(hist):,}")

    hist_dims = {}
    for dim in _HIST_DIMS:
        stats = _ic_stats(hist[dim], hist["fwd_return"])
        stability = _rolling_stability(hist, dim)
        spread = _quintile_spread(hist, dim)
        hist_dims[dim] = {
            **stats,
            "ic_stability_std": stability,
            "quintile_spread": spread,
            "source": "historical",
            "verdict": _verdict(stats["ic"], stats.get("pvalue"), stats["n"]),
        }

    # ── Source 3: Training records (closed trades with actual pnl) ────────────
    log.info("Loading training records...")
    training = _load_training()
    training_n = len(training)
    log.info("Loaded %s training records with signal_scores", training_n)

    training_dims = {}
    if not training.empty:
        training["fwd_return"] = _clip_outliers(training["fwd_return"])
        all_training_dims = [c for c in training.columns if c != "fwd_return"]
        for dim in all_training_dims:
            stats = _ic_stats(training[dim], training["fwd_return"])
            training_dims[dim] = {
                **stats,
                "ic_stability_std": None,
                "quintile_spread": _quintile_spread(training, dim) if training_n >= 100 else None,
                "source": "training_trades",
                "verdict": _verdict(stats["ic"], stats.get("pvalue"), stats["n"]),
            }

    # ── Merge: live IC is primary; historical enriches stable dims ─────────────
    merged = {}
    all_dims = set(live_dims) | set(hist_dims) | set(training_dims)
    for dim in sorted(all_dims):
        # Pick the most data-rich / current source
        if dim in live_dims and live_dims[dim]["ic"] is not None:
            entry = dict(live_dims[dim])
        elif dim in training_dims and training_dims[dim]["ic"] is not None:
            entry = dict(training_dims[dim])
        elif dim in hist_dims and hist_dims[dim]["ic"] is not None:
            entry = dict(hist_dims[dim])
        else:
            entry = live_dims.get(dim) or hist_dims.get(dim) or training_dims.get(dim) or {}

        # Enrich with historical stability / quintile spread where available
        if dim in hist_dims:
            if entry.get("ic_stability_std") is None:
                entry["ic_stability_std"] = hist_dims[dim].get("ic_stability_std")
            if entry.get("quintile_spread") is None:
                entry["quintile_spread"] = hist_dims[dim].get("quintile_spread")
            entry["historical_ic"] = hist_dims[dim].get("ic")

        if dim in training_dims:
            entry["trade_ic"] = training_dims[dim].get("ic")
            entry["trade_n"] = training_dims[dim].get("n")

        merged[dim] = entry

    keep = [d for d, v in merged.items() if v.get("verdict") == "KEEP"]
    reduce = [d for d, v in merged.items() if v.get("verdict") == "REDUCE_WEIGHT"]
    remove = [d for d, v in merged.items() if v.get("verdict") == "REMOVE"]
    negative = [d for d, v in merged.items() if v.get("verdict") == "NEGATIVE_IC"]

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "live_ic_n": live_n,
        "live_ic_updated": live_updated,
        "historical_n": len(hist),
        "training_n": training_n,
        "dimensions": merged,
        "summary": {
            "keep": keep,
            "reduce_weight": reduce,
            "remove": remove,
            "negative_ic": negative,
            "total_analyzed": len(merged),
            "equal_weights_active": live_ic_cache.get("using_equal_weights", True),
        },
    }
    return report


# ── Output ─────────────────────────────────────────────────────────────────────


def print_report(report: dict) -> None:
    dims = report["dimensions"]
    print("\n" + "=" * 78)
    print("ALPHA VALIDATION — PER-DIMENSION IC ANALYSIS".center(78))
    print("=" * 78)
    print(f"\nLive IC (Decifer 3.0): n={report['live_ic_n']}  updated={report['live_ic_updated'][:10]}")
    print(f"Historical records:    n={report['historical_n']:,}  (old signal era — basic dims only)")
    print(f"Closed trade records:  n={report['training_n']}  (actual pnl_pct)")
    equal_flag = "  ⚠  equal weights still active (paper mode)" if report["summary"]["equal_weights_active"] else ""
    print(f"Generated: {report['generated_at'][:19]}{equal_flag}\n")

    header = f"  {'Dimension':<20} {'Live IC':>8} {'Hist IC':>8} {'Trade IC':>9} {'Stability':>10} {'Q-Sprd':>7}  Verdict"
    print(header)
    print("-" * 78)

    _order = {"KEEP": 0, "REDUCE_WEIGHT": 1, "NEGATIVE_IC": 2, "REMOVE": 3, "INSUFFICIENT_DATA": 4}
    for dim, v in sorted(dims.items(), key=lambda x: (_order.get(x[1].get("verdict", "INSUFFICIENT_DATA"), 5), -(x[1].get("ic") or 0))):
        live_s = f"{v['ic']:+.4f}" if v.get("ic") is not None else "      -"
        hist_s = f"{v['historical_ic']:+.4f}" if v.get("historical_ic") is not None else "      -"
        trade_s = f"{v['trade_ic']:+.4f}" if v.get("trade_ic") is not None else "       -"
        stab_s = f"{v['ic_stability_std']:.4f}" if v.get("ic_stability_std") is not None else "         -"
        qs_s = f"{v['quintile_spread']:+.4f}" if v.get("quintile_spread") is not None else "      -"
        verdict = v.get("verdict", "?")
        markers = {"KEEP": "✓", "REDUCE_WEIGHT": "~", "NEGATIVE_IC": "↓", "REMOVE": "✗", "INSUFFICIENT_DATA": "?"}
        m = markers.get(verdict, "?")
        print(f"  {dim:<20} {live_s:>8} {hist_s:>8} {trade_s:>9} {stab_s:>10} {qs_s:>7}  {m} {verdict}")

    s = report["summary"]
    print(f"\n  KEEP         ({len(s['keep'])}): {', '.join(s['keep']) or 'none'}")
    print(f"  REDUCE       ({len(s['reduce_weight'])}): {', '.join(s['reduce_weight']) or 'none'}")
    print(f"  NEGATIVE IC  ({len(s['negative_ic'])}): {', '.join(s['negative_ic']) or 'none'}")
    print(f"  REMOVE       ({len(s['remove'])}): {', '.join(s['remove']) or 'none'}")

    if s["equal_weights_active"]:
        print("\n  NOTE: Equal weights are active (paper mode). Live IC is diagnostic only.")
        print("        Activate IC weighting when trade volume justifies it (current gate: 200 trades — MET).")
    print("\n" + "=" * 78 + "\n")


def _atomic_write(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    dir_ = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_report(report: dict, path: str = _OUT_FILE) -> None:
    _atomic_write(path, report)
    log.info("Report saved → %s", path)
    try:
        chief_payload = {
            "type": "alpha_validation",
            "title": "Per-Dimension Alpha Validation",
            "generated_at": report["generated_at"],
            "summary": report["summary"],
            "dimensions": report["dimensions"],
        }
        _atomic_write(_CHIEF_OUT, chief_payload)
        log.info("Chief state written → %s", _CHIEF_OUT)
    except Exception as e:
        log.warning("Chief state write failed: %s", e)


# ── CLI ────────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Decifer Alpha Validation")
    parser.add_argument("--no-save", action="store_true", help="Print report without saving to disk")
    args = parser.parse_args()

    report = run_alpha_validation()
    print_report(report)
    if not args.no_save:
        save_report(report)
        print(f"Report saved to: {_OUT_FILE}")
    sys.exit(0)
