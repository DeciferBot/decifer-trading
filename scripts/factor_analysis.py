"""
scripts/factor_analysis.py — Full IC factor analysis on signals_log.jsonl.

Computes:
  1. Per-dimension IC at +1d, +5d, +10d, +20d forward horizons
  2. Regime-conditional IC breakdown
  3. IC decay curve (predictive signal persistence)
  4. Quantile monotonicity check (do higher scores → higher returns?)

Output: data/factor_analysis_report.json + formatted stdout summary.

Run:  python3 scripts/factor_analysis.py
Re-run is fast (price data cached to data/factor_analysis_price_cache.json).

Usage note: Pass --clear-cache to force a fresh price download.
"""

import json
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import numpy as np

# ── Paths ──────────────────────────────────────────────────────────────────────
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SIGNALS_LOG = os.path.join(_REPO, "data", "signals_log.jsonl")
_PRICE_CACHE = os.path.join(_REPO, "data", "factor_analysis_price_cache.json")
_REPORT = os.path.join(_REPO, "data", "factor_analysis_report.json")

HORIZONS = [1, 5, 10, 20]  # trading-day forward return horizons
MIN_N = 30  # minimum paired observations for IC to be meaningful
QUANTILE_BUCKETS = 5  # score buckets for monotonicity check

# Core 9 dims that must be present to admit a record
_CORE = {"trend", "momentum", "squeeze", "flow", "breakout", "mtf", "news", "social", "reversion"}

# Alias: older records use "directional" for what is now "trend"
_ALIASES = {"directional": "trend"}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    return float(np.corrcoef(rx, ry)[0, 1])


def _dir_sign(rec: dict) -> int:
    return -1 if rec.get("direction") == "SHORT" else 1


def _scan_date(rec: dict) -> str | None:
    ts = rec.get("ts", "")
    try:
        return str(datetime.fromisoformat(ts.replace("Z", "+00:00")).date())
    except Exception:
        return None


# ── Data loading ───────────────────────────────────────────────────────────────

def load_records() -> list[dict]:
    """Load all valid signals_log records that have a populated score_breakdown."""
    records = []
    with open(_SIGNALS_LOG) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            bd = r.get("score_breakdown", {})
            if not bd:
                continue
            # Normalise aliases
            for alias, canonical in _ALIASES.items():
                if alias in bd and canonical not in bd:
                    bd[canonical] = bd.pop(alias)
            # Require core dimensions
            if not _CORE.issubset(bd.keys()):
                continue
            r["score_breakdown"] = bd
            records.append(r)
    return records


def fetch_price_cache(records: list[dict]) -> dict[str, dict[str, float]]:
    """
    Return {symbol: {date_str: close_price}} covering the full signal date range.
    Loads from disk cache if available; downloads via yfinance otherwise.
    """
    if "--clear-cache" not in sys.argv and os.path.exists(_PRICE_CACHE):
        print(f"Loading price cache from {_PRICE_CACHE} ...")
        with open(_PRICE_CACHE) as f:
            return json.load(f)

    import yfinance as yf

    by_symbol: dict[str, list[datetime]] = defaultdict(list)
    for r in records:
        sym = r.get("symbol")
        ts = r.get("ts", "")
        if not sym or not ts:
            continue
        try:
            by_symbol[sym].append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
        except Exception:
            pass

    print(f"Fetching daily bars for {len(by_symbol)} symbols ...")
    cache: dict[str, dict[str, float]] = {}
    for i, (sym, dates) in enumerate(sorted(by_symbol.items()), 1):
        if i % 100 == 0:
            print(f"  {i}/{len(by_symbol)} ...")
        start = (min(dates) - timedelta(days=2)).strftime("%Y-%m-%d")
        end = (max(dates) + timedelta(days=35)).strftime("%Y-%m-%d")
        try:
            df = yf.download(sym, start=start, end=end, interval="1d",
                             progress=False, auto_adjust=True)
            if df is None or len(df) < 2:
                continue
            if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
                df.columns = df.columns.get_level_values(0)
            close = df["Close"].dropna()
            cache[sym] = {str(d.date()): float(p) for d, p in zip(close.index, close.values)}
        except Exception:
            pass

    with open(_PRICE_CACHE, "w") as f:
        json.dump(cache, f)
    print(f"Cached {len(cache)} symbols to {_PRICE_CACHE}")
    return cache


def get_fwd_return(price_map: dict[str, float], scan_date: str, entry_price: float,
                   horizon: int) -> float | None:
    """Return direction-neutral forward return at `horizon` trading days, or None."""
    dates = sorted(d for d in price_map if d > scan_date)
    if len(dates) < horizon:
        return None
    exit_price = price_map[dates[horizon - 1]]
    if entry_price <= 0 or exit_price <= 0:
        return None
    return (exit_price - entry_price) / entry_price


# ── IC computation ─────────────────────────────────────────────────────────────

def compute_ic_for_group(records: list[dict], price_cache: dict, horizon: int) -> dict:
    """
    Compute Spearman IC per dimension for a group of records at a given horizon.
    Returns {dim: ic_float | None}.
    """
    today = datetime.now(UTC).date()
    dim_scores: dict[str, list[float]] = defaultdict(list)
    returns: list[float] = []

    for r in records:
        sd = _scan_date(r)
        if not sd:
            continue
        # Record must be old enough that +horizon trading days have passed
        try:
            age_days = (today - datetime.strptime(sd, "%Y-%m-%d").date()).days
        except Exception:
            continue
        if age_days < horizon + 2:
            continue

        sym = r.get("symbol", "")
        entry_price = r.get("price")
        if not sym or not entry_price or sym not in price_cache:
            continue

        fwd = get_fwd_return(price_cache[sym], sd, float(entry_price), horizon)
        if fwd is None or not np.isfinite(fwd):
            continue

        adj_fwd = _dir_sign(r) * fwd
        returns.append(adj_fwd)
        bd = r["score_breakdown"]
        for dim in bd:
            dim_scores[dim].append(float(bd[dim]))
        # Zero-fill dims not in this record
        for dim in (dim_scores.keys() - bd.keys()):
            dim_scores[dim].append(0.0)

    if len(returns) < MIN_N:
        return {}

    ret_arr = np.array(returns)
    result = {}
    for dim, scores in dim_scores.items():
        if len(scores) != len(returns):
            continue
        sc_arr = np.array(scores)
        if np.std(sc_arr) < 1e-9:
            result[dim] = 0.0
            continue
        try:
            result[dim] = round(_spearman(sc_arr, ret_arr), 4)
        except Exception:
            result[dim] = None
    result["_n"] = len(returns)
    return result


def compute_quantile_returns(records: list[dict], price_cache: dict,
                              dim: str, horizon: int = 5) -> list[dict] | None:
    """
    Split records into QUANTILE_BUCKETS by `dim` score.
    Returns list of {bucket, mean_score, mean_return, n} or None if insufficient data.
    """
    today = datetime.now(UTC).date()
    pairs = []
    for r in records:
        sd = _scan_date(r)
        if not sd:
            continue
        try:
            age_days = (today - datetime.strptime(sd, "%Y-%m-%d").date()).days
        except Exception:
            continue
        if age_days < horizon + 2:
            continue
        sym = r.get("symbol", "")
        ep = r.get("price")
        bd = r.get("score_breakdown", {})
        if not sym or not ep or sym not in price_cache or dim not in bd:
            continue
        fwd = get_fwd_return(price_cache[sym], sd, float(ep), horizon)
        if fwd is None or not np.isfinite(fwd):
            continue
        pairs.append((float(bd[dim]), _dir_sign(r) * fwd))

    if len(pairs) < MIN_N * QUANTILE_BUCKETS:
        return None

    scores = np.array([p[0] for p in pairs])
    rets = np.array([p[1] for p in pairs])
    percentiles = np.percentile(scores, np.linspace(0, 100, QUANTILE_BUCKETS + 1))

    buckets = []
    for i in range(QUANTILE_BUCKETS):
        lo, hi = percentiles[i], percentiles[i + 1]
        mask = (scores >= lo) & (scores <= hi) if i == QUANTILE_BUCKETS - 1 else (scores >= lo) & (scores < hi)
        if mask.sum() == 0:
            continue
        buckets.append({
            "bucket": i + 1,
            "score_range": [round(float(lo), 2), round(float(hi), 2)],
            "mean_score": round(float(scores[mask].mean()), 2),
            "mean_return_pct": round(float(rets[mask].mean() * 100), 3),
            "n": int(mask.sum()),
        })
    return buckets


# ── Report formatting ──────────────────────────────────────────────────────────

def _fmt_ic(ic) -> str:
    if ic is None:
        return "  N/A "
    return f"{ic:+.3f}"


def print_summary(report: dict) -> None:
    dims = sorted(k for k in report["overall"].get("h1", {}).keys() if not k.startswith("_"))

    print("\n" + "=" * 72)
    print("  DECIFER FACTOR ANALYSIS REPORT")
    print(f"  Records analysed: {report['meta']['total_records_analysed']}  |  "
          f"Symbols: {report['meta']['unique_symbols']}  |  "
          f"Date range: {report['meta']['date_range']}")
    print("=" * 72)

    # Overall IC table
    print(f"\n{'DIMENSION':<20} {'IC +1d':>8} {'IC +5d':>8} {'IC +10d':>9} {'IC +20d':>9}  FLAG")
    print("-" * 72)
    for dim in dims:
        ic1 = report["overall"].get("h1", {}).get(dim)
        ic5 = report["overall"].get("h5", {}).get(dim)
        ic10 = report["overall"].get("h10", {}).get(dim)
        ic20 = report["overall"].get("h20", {}).get(dim)
        flag = ""
        if ic5 is not None and ic5 > 0.05:
            flag = "✓ SIGNAL"
        elif ic5 is not None and 0.03 <= ic5 <= 0.05:
            flag = "~ MARGINAL"
        elif ic5 is not None and ic5 < 0:
            flag = "✗ NEGATIVE"
        print(f"  {dim:<18} {_fmt_ic(ic1):>8} {_fmt_ic(ic5):>8} {_fmt_ic(ic10):>9} {_fmt_ic(ic20):>9}  {flag}")

    n1 = report["overall"].get("h1", {}).get("_n", 0)
    n5 = report["overall"].get("h5", {}).get("_n", 0)
    print(f"\n  N: +1d={n1}  +5d={n5}")

    # Regime breakdown (IC at +5d only)
    print(f"\n{'REGIME IC (+5d)':<22}", end="")
    for dim in dims[:8]:
        print(f"  {dim[:7]:>7}", end="")
    print()
    print("-" * 72)
    for regime, ic_map in sorted(report.get("by_regime", {}).items()):
        h5 = ic_map.get("h5", {})
        n = h5.get("_n", 0)
        print(f"  {regime:<20}", end="")
        for dim in dims[:8]:
            v = h5.get(dim)
            print(f"  {_fmt_ic(v):>7}", end="")
        print(f"  n={n}")

    print("\n" + "=" * 72)
    print("  Full report: data/factor_analysis_report.json")
    print("=" * 72 + "\n")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print("Loading signal records ...")
    records = load_records()
    print(f"Loaded {len(records)} valid records.")

    price_cache = fetch_price_cache(records)

    regimes = defaultdict(list)
    for r in records:
        regimes[r.get("regime", "unknown")].append(r)

    # Overall IC at each horizon
    print("\nComputing overall IC per horizon ...")
    overall = {}
    for h in HORIZONS:
        print(f"  +{h}d ...", end=" ", flush=True)
        ic = compute_ic_for_group(records, price_cache, h)
        overall[f"h{h}"] = ic
        n = ic.get("_n", 0)
        print(f"n={n}")

    # Regime-conditional IC at each horizon
    print("\nComputing regime-conditional IC ...")
    by_regime = {}
    for regime, recs in sorted(regimes.items()):
        print(f"  {regime} (n={len(recs)}) ...")
        by_regime[regime] = {}
        for h in HORIZONS:
            ic = compute_ic_for_group(recs, price_cache, h)
            by_regime[regime][f"h{h}"] = ic

    # Quantile monotonicity check at +5d for top dimensions
    print("\nComputing quantile returns (5-bucket) at +5d ...")
    key_dims = ["trend", "momentum", "breakout", "flow", "reversion", "overnight_drift"]
    quantiles = {}
    for dim in key_dims:
        q = compute_quantile_returns(records, price_cache, dim, horizon=5)
        if q:
            quantiles[dim] = q
            print(f"  {dim}: {len(q)} buckets")

    # Build report
    unique_symbols = len({r.get("symbol") for r in records})
    dates = [_scan_date(r) for r in records if _scan_date(r)]
    date_range = f"{min(dates)} → {max(dates)}" if dates else "unknown"

    report = {
        "meta": {
            "generated_at": datetime.now(UTC).isoformat(),
            "total_records_analysed": len(records),
            "unique_symbols": unique_symbols,
            "date_range": date_range,
            "horizons_days": HORIZONS,
            "min_n_for_ic": MIN_N,
        },
        "overall": overall,
        "by_regime": by_regime,
        "quantile_returns": quantiles,
    }

    with open(_REPORT, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport written to {_REPORT}")

    print_summary(report)


if __name__ == "__main__":
    main()
