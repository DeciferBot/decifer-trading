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
