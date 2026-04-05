"""
Signal Correlation Analysis
----------------------------
Reads data/signals_log.jsonl and reports:
  1. Pearson correlation matrix across all scored dimensions
  2. High-correlation pairs (|r| > 0.6)
  3. Regime-split matrices (BEAR_TRENDING vs BULL_TRENDING)
  4. PCA effective dimensionality (how many dims explain 90% of variance)

Read-only — no changes to trading state.

Usage:
    python tools/signal_correlation.py
    python tools/signal_correlation.py --threshold 0.5
"""

import json
import sys
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA

SIGNALS_LOG = Path(__file__).parent.parent / "data" / "signals_log.jsonl"
DIMS = ["trend", "momentum", "squeeze", "flow", "breakout", "mtf", "news", "social", "reversion"]


def load_signals(path: Path) -> pd.DataFrame:
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            sb = row.get("score_breakdown", {})
            if not sb:
                continue
            entry = {"regime": row.get("regime", "UNKNOWN"), "score": row.get("score", 0)}
            for dim in DIMS:
                entry[dim] = sb.get(dim, 0)
            records.append(entry)
    return pd.DataFrame(records)


def print_matrix(df: pd.DataFrame, label: str, threshold: float) -> list:
    """Print correlation matrix and return list of high-correlation pairs."""
    corr = df[DIMS].corr(method="pearson")
    high_pairs = []

    print(f"\n{'='*72}")
    print(f"  {label}  (n={len(df):,})")
    print(f"{'='*72}")

    # Header
    col_w = 7
    header = f"{'':12}" + "".join(f"{d[:6]:>{col_w}}" for d in DIMS)
    print(header)
    print("-" * len(header))

    for row_dim in DIMS:
        row_str = f"{row_dim:<12}"
        for col_dim in DIMS:
            val = corr.loc[row_dim, col_dim]
            marker = "*" if row_dim != col_dim and abs(val) >= threshold else " "
            row_str += f"{val:>{col_w-1}.2f}{marker}"
        print(row_str)

    # Collect high pairs (upper triangle only)
    for i, d1 in enumerate(DIMS):
        for d2 in DIMS[i+1:]:
            val = corr.loc[d1, d2]
            if abs(val) >= threshold:
                high_pairs.append((d1, d2, val))

    return high_pairs


def pca_dims(df: pd.DataFrame, variance_target: float = 0.90) -> int:
    """Return number of components needed to explain variance_target of variance."""
    X = df[DIMS].values
    # Standardize
    X = (X - X.mean(axis=0)) / (X.std(axis=0) + 1e-9)
    pca = PCA(n_components=len(DIMS))
    pca.fit(X)
    cumvar = np.cumsum(pca.explained_variance_ratio_)
    n_dims = int(np.searchsorted(cumvar, variance_target)) + 1
    return n_dims, pca.explained_variance_ratio_


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=float, default=0.6,
                        help="Flag pairs with |r| above this value (default 0.6)")
    args = parser.parse_args()

    if not SIGNALS_LOG.exists():
        print(f"ERROR: {SIGNALS_LOG} not found")
        sys.exit(1)

    df = load_signals(SIGNALS_LOG)
    if df.empty:
        print("No records with populated score_breakdown found.")
        sys.exit(0)

    print(f"\nLoaded {len(df):,} records with score_breakdown data.")
    print(f"Regimes present: {df['regime'].value_counts().to_dict()}")
    print(f"High-correlation threshold: |r| >= {args.threshold}")

    # --- Full matrix ---
    high_pairs = print_matrix(df, "ALL REGIMES", args.threshold)

    # --- Regime split ---
    for regime in ["BULL_TRENDING", "BEAR_TRENDING", "CHOPPY", "NEUTRAL"]:
        sub = df[df["regime"] == regime]
        if len(sub) >= 30:
            print_matrix(sub, regime, args.threshold)

    # --- High correlation summary ---
    print(f"\n{'='*72}")
    print(f"  HIGH-CORRELATION PAIRS (|r| >= {args.threshold})")
    print(f"{'='*72}")
    if not high_pairs:
        print(f"  None found above threshold {args.threshold}")
    else:
        for d1, d2, val in sorted(high_pairs, key=lambda x: -abs(x[2])):
            direction = "positive" if val > 0 else "negative"
            print(f"  {d1:<12} <-> {d2:<12}  r={val:+.3f}  ({direction})")

    # --- PCA effective dimensionality ---
    n_eff, evr = pca_dims(df)
    print(f"\n{'='*72}")
    print(f"  PCA EFFECTIVE DIMENSIONALITY")
    print(f"{'='*72}")
    print(f"  Components needed for 90% variance: {n_eff} of {len(DIMS)}")
    print(f"  Per-component explained variance:")
    for i, v in enumerate(evr, 1):
        bar = "#" * int(v * 40)
        print(f"    PC{i:>2}: {v:.3f}  {bar}")

    # --- Verdict ---
    print(f"\n{'='*72}")
    print(f"  VERDICT")
    print(f"{'='*72}")
    if n_eff <= 4:
        print(f"  Effective dims = {n_eff}. Signal compression is HIGH.")
        print("  The 9 scored dimensions behave like ~4 independent factors.")
        print("  IC weighting is the right fix — let it differentiate within clusters.")
    elif n_eff <= 6:
        print(f"  Effective dims = {n_eff}. Moderate compression.")
        print("  Some redundancy exists but not severe.")
    else:
        print(f"  Effective dims = {n_eff}. Dimensions are largely independent.")

    if high_pairs:
        tech_pairs = [(d1, d2, r) for d1, d2, r in high_pairs
                      if d1 in ["trend","momentum","squeeze","flow","breakout"]
                      and d2 in ["trend","momentum","squeeze","flow","breakout"]]
        if tech_pairs:
            print(f"\n  {len(tech_pairs)} high-correlation pair(s) within the technical cluster:")
            for d1, d2, val in tech_pairs:
                print(f"    {d1} <-> {d2}: r={val:+.3f}")
            print("  This confirms score inflation when all tech dims fire simultaneously.")
        else:
            print("\n  No high-correlation pairs within the technical cluster.")
            print("  Technical dims are more independent than expected.")


if __name__ == "__main__":
    main()
