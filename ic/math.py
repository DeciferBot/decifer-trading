# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  ic/math.py                                ║
# ║   Pure numerical helpers: Spearman rank correlation and     ║
# ║   z-score normalisation.  No I/O, no config, no globals.    ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

import numpy as np


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    """
    Compute Spearman rank correlation.

    Uses scipy.stats.spearmanr when available (handles ties correctly).
    Falls back to a numpy-only approximation that is exact when there are no ties.
    """
    try:
        from scipy.stats import spearmanr

        corr, _ = spearmanr(x, y)
        return float(corr) if np.isfinite(corr) else 0.0
    except ImportError:
        pass
    # numpy fallback — rank via argsort(argsort()), no tie-correction
    n = len(x)
    if n < 3:
        return 0.0
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    d = rx - ry
    denom = n * (n * n - 1)
    return float(1.0 - 6.0 * np.sum(d * d) / denom) if denom > 0 else 0.0


def _zscore_array(arr: np.ndarray) -> np.ndarray:
    """Standardise array to zero mean / unit variance.  Returns zeros if std < 1e-9."""
    std = float(np.std(arr))
    if std < 1e-9:
        return np.zeros_like(arr, dtype=float)
    return (arr - np.mean(arr)) / std
