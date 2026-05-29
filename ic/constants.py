# ╔══════════════════════════════════════════════════════════════╗
# ║   <>  DECIFER  —  ic/constants.py                           ║
# ║   Shared constants, file paths, and CONFIG helper for the   ║
# ║   IC package.  All module-level state for the IC subsystem  ║
# ║   lives here to keep path resolution consistent across the  ║
# ║   split modules.                                            ║
# ║   Inventor: AMIT CHOPRA                                      ║
# ╚══════════════════════════════════════════════════════════════╝

import logging
import os

log = logging.getLogger("decifer.ic_calculator")

# ── Dimension inventory ────────────────────────────────────────────────────────

DIMENSIONS = [
    "trend",
    "momentum",
    "squeeze",
    "flow",
    "breakout",
    "mtf",
    "news",
    "social",
    "reversion",
    "iv_skew",
    "pead",
    "short_squeeze",
    "overnight_drift",
    "analyst_revision",
    "insider_buying",
]
# Core 9 dimensions that have always been logged — the minimum required to
# admit a record into IC calculation.  Newer dimensions (iv_skew, pead,
# short_squeeze) are backfilled with 0 for records that predate their addition.
_CORE_DIMENSIONS = [
    "trend",
    "momentum",
    "squeeze",
    "flow",
    "breakout",
    "mtf",
    "news",
    "social",
    "reversion",
]
_N = len(DIMENSIONS)
EQUAL_WEIGHTS: dict = {d: 1.0 / _N for d in DIMENSIONS}

# Baseline strategic weights — used when IC is not trusted for live scoring.
# These reflect the intended signal architecture before IC data has accumulated
# or when the computed IC weights fail a validity gate (concentration, insufficient
# dates, degenerate HHI-cap redistribution).
#
# NOT equal weights: dimensions with consistently zero/inactive IC (pead,
# analyst_revision, insider_buying) receive zero weight.  News and social receive
# the highest prior weight based on Phase 1 IC evidence; the remaining active
# dimensions receive proportional priors reflecting the orthogonal architecture.
#
# Sum = 1.00 (verified).
BASELINE_WEIGHTS: dict = {
    "trend":            0.13,
    "momentum":         0.10,
    "squeeze":          0.09,
    "flow":             0.08,
    "breakout":         0.09,
    "mtf":              0.08,
    "news":             0.15,
    "social":           0.12,
    "reversion":        0.07,
    "iv_skew":          0.05,
    "pead":             0.00,
    "short_squeeze":    0.04,
    "overnight_drift":  0.00,  # BLOCKED CRITICAL: negative IC in both candidate and execution
    "analyst_revision": 0.00,
    "insider_buying":   0.00,
}

# ── File paths ─────────────────────────────────────────────────────────────────
# _BASE must resolve to the repo root (parent of the `ic/` package dir).
# Going up TWO levels from this file: ic/constants.py → ic/ → repo root.
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IC_WEIGHTS_FILE = os.path.join(_BASE, "data", "ic_weights.json")
IC_HISTORY_FILE = os.path.join(_BASE, "data", "ic_weights_history.jsonl")
SIGNALS_LOG_FILE = os.path.join(_BASE, "data", "signals_log.jsonl")
IC_LIVE_FILE = os.path.join(_BASE, "data", "ic_weights_live.json")
IC_LIVE_HISTORY_FILE = os.path.join(_BASE, "data", "ic_weights_live_history.jsonl")
_TRADES_FILE = os.path.join(_BASE, "data", "trades.json")
_LIVE_IC_REPORT_FILE = os.path.join(_BASE, "data", "live_ic_report.json")

# ── Tuning constants ───────────────────────────────────────────────────────────

ROLLING_WINDOW = 60  # records to use for IC calculation
MIN_VALID = 20  # minimum records with forward returns before IC is trusted
LIVE_IC_MILESTONE = 50  # trades needed before live-vs-historical comparison runs


def _ic_cfg(key: str, default):
    """Read a value from CONFIG['ic_calculator'], falling back to *default*."""
    try:
        from config import CONFIG

        return CONFIG.get("ic_calculator", {}).get(key, default)
    except Exception:
        return default
